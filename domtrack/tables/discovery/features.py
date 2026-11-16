"""Computational feature extraction for table discovery.

Every node, column, page and column pair is described by a flat dictionary of
features.  Those dictionaries are turned into numeric vectors by
:class:`FeatureVectorizer`; the vectors are the input to the merge phase
(atoms -> columns), prototype assignment (cells -> column prototypes) and grid
detection (rows -> table).

The module only uses the standard library plus :mod:`numpy`.  Nothing here
re-implements a library algorithm: statistics, entropy and cosine similarity
are the ordinary formulas.
"""

from __future__ import annotations

import math
import os
import re
import statistics
import unicodedata
from collections import Counter
from typing import Iterable, Mapping, Optional, Sequence
from urllib.parse import parse_qs, urlsplit

import numpy as np

from .model import Node, clean, is_hidden_by_attrs


# ── feature key groups (mirrors the specification) ─────────────────────────

NODE_STRUCTURAL = (
    "tag", "depth", "parent_tag", "child_count", "sibling_count",
    "index_in_parent", "has_id", "class_count", "is_heading", "is_img",
    "is_link", "is_semantic_tag",
)
NODE_ATTR_SEMANTIC = (
    "role", "itemprop", "itemtype", "aria_label", "aria_labelledby",
    "aria_valuenow", "in_aria_hidden", "data_attr_keys",
)
NODE_ATTR_CONTENT = (
    "href", "href_path_segments", "href_query_keys", "href_is_absolute",
    "src", "src_extension", "alt", "title_attr", "value_attr", "input_type",
    "placeholder_len",
)
NODE_TEXT = (
    "text_len", "token_count", "dominant_script", "script_count",
    "digit_script", "digit_count", "letter_count", "punct_count",
    "symbol_count", "has_currency_symbol", "digit_ratio", "letter_ratio",
    "punct_ratio", "symbol_ratio",
)
NODE_VISIBILITY = ("is_hidden_by_attrs", "ever_visible")
NODE_ATOM = ("atype",)
NODE_CAPTURE = (
    "rect_x", "rect_y", "rect_w", "rect_h", "offset_parent_null",
    "scroll_width", "client_width", "font_size", "font_weight", "is_bold",
    "line_height", "text_transform", "letter_spacing", "color", "bg_color",
    "cursor", "inner_text_len", "is_truncated",
)
PAGE_FEATURES = (
    "html_lang", "html_dir", "viewport_width", "viewport_height",
    "device_pixel_ratio", "page_url",
)

COLUMN_FEATURES = (
    "coverage", "null_rate", "cardinality", "mean_char_len", "std_char_len",
    "mean_token_count", "std_token_count", "entropy", "numeric_parsed_rate",
    "numeric_mean", "numeric_std", "numeric_monotonic",
    "is_numeric", "is_currency", "is_percent", "is_url", "is_image_path",
    "is_date_like", "is_phone_like", "is_short_text", "is_free_text",
    "type_entropy",
    "unique_token_ratio", "token_vocab_size", "categorical_tokens",
    "free_text_tokens", "discovered_units", "unit_consistency",
    "numeric_prefix_suffix", "common_prefix_len",
    "presence_consistency", "value_stability", "position_stability",
    "neighbor_stability", "mutex_rate", "jaccard_with_other",
    "prefix_match_rate", "numeric_correlation",
)

PAIRWISE_FEATURES = (
    "semantic_class_jaccard", "tag_match", "atype_match", "itemprop_match",
    "role_match", "type_sig_cosine", "layout_dist", "neighbor_overlap",
    "discovered_unit_match", "mutex_clean",
)

TYPE_FLAGS = (
    "is_numeric", "is_currency", "is_percent", "is_url", "is_image_path",
    "is_date_like", "is_phone_like", "is_short_text", "is_free_text",
)

SEMANTIC_TAGS = frozenset(
    {
        "article", "section", "nav", "header", "footer", "main", "aside",
        "figure", "figcaption", "table", "thead", "tbody", "tfoot", "tr",
        "td", "th", "ul", "ol", "li", "dl", "dt", "dd", "form", "label",
        "button", "time", "address", "blockquote", "details", "summary",
    }
)

CURRENCY_SYMBOLS = frozenset("$€£¥₹₽₺₴₦₩₪₫₱฿₡₲₵₸₼₾﷼")
CURRENCY_WORDS = (
    "تومان", "ریال", "درهم", "دلار", "یورو", "لیر", "پوند", "فرانک",
    "usd", "eur", "gbp", "irr", "toman", "rial",
)
PERCENT_MARKERS = ("%", "٪", "درصد")
IMAGE_EXTS = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".avif", ".ico"}
)
DATE_MARKERS = (
    "امروز", "دیروز", "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد",
    "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
)

UNIT_TOKENS = (
    "تومان", "ریال", "درهم", "دلار", "یورو", "درصد", "کیلومتر", "کیلوگرم",
    "گرم", "متر", "سانتیمتر", "لیتر", "عدد", "ساعت", "روز", "ماه", "سال",
    "اینچ", "گیگابایت", "مگابایت", "ترابایت", "gb", "mb", "tb", "px", "cm",
    "mm", "kg", "ml",
)

_DIGIT_TRANS = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"
)
_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_DATE_RE = re.compile(r"\d{1,4}[-/.]\d{1,2}(?:[-/.]\d{1,4})?")
_URL_RE = re.compile(r"^(?:[a-z][a-z0-9+.-]*://|//|/|www\.)", re.IGNORECASE)


# ── text / unicode helpers ─────────────────────────────────────────────────

def normalize_digits(text: str) -> str:
    """Map Persian and Arabic-Indic digits to ASCII digits."""
    return (text or "").translate(_DIGIT_TRANS)


def parse_number(value: str) -> Optional[float]:
    """Return the first number in ``value`` after digit/separator cleanup."""
    if not value:
        return None
    text = normalize_digits(value)
    text = text.replace(",", "").replace("٬", "").replace("٫", ".")
    text = text.replace("\u200c", "").replace(" ", "")
    match = _NUMBER_RE.search(text)
    return float(match.group()) if match else None


def _char_script(ch: str) -> str:
    code = ord(ch)
    if 0x0660 <= code <= 0x0669 or 0x06F0 <= code <= 0x06F9:
        return "arabic"
    name = unicodedata.name(ch, "")
    if not name:
        return "other"
    for prefix, label in (
        ("ARABIC", "arabic"), ("LATIN", "latin"), ("CYRILLIC", "cyrillic"),
        ("GREEK", "greek"), ("HEBREW", "hebrew"), ("DEVANAGARI", "devanagari"),
        ("THAI", "thai"), ("HIRAGANA", "japanese"), ("KATAKANA", "japanese"),
        ("CJK", "han"), ("HANGUL", "hangul"),
    ):
        if name.startswith(prefix):
            return label
    return "other"


def _digit_script(text: str) -> str:
    labels = set()
    for ch in text:
        code = ord(ch)
        if 0x30 <= code <= 0x39:
            labels.add("ascii")
        elif 0x0660 <= code <= 0x0669:
            labels.add("arabic")
        elif 0x06F0 <= code <= 0x06F9:
            labels.add("persian")
    if not labels:
        return "none"
    return labels.pop() if len(labels) == 1 else "mixed"


def _subtree_text(node: Node) -> str:
    if not node.tag:
        return node.text or ""
    parts: list[str] = []

    def walk(current: Node) -> None:
        if not current.tag:
            parts.append(current.text or "")
            return
        for child in current.children:
            walk(child)

    walk(node)
    return clean(" ".join(parts))


def _entropy(counter: Counter) -> float:
    total = sum(counter.values())
    if total <= 0:
        return 0.0
    return -sum(
        (count / total) * math.log2(count / total) for count in counter.values()
    )


def _safe_stdev(values: Sequence[float]) -> float:
    return float(statistics.pstdev(values)) if len(values) > 1 else 0.0


def _safe_mean(values: Sequence[float]) -> float:
    return float(statistics.fmean(values)) if values else 0.0


def _jaccard(a: Iterable, b: Iterable) -> float:
    left, right = set(a), set(b)
    if not left and not right:
        return 1.0
    union = left | right
    return len(left & right) / len(union) if union else 1.0


# ── per-node features ──────────────────────────────────────────────────────

def _structural(node: Node, root: Optional[Node]) -> dict:
    depth = 0
    current = node.parent
    while current is not None:
        if current.tag:
            depth += 1
        current = current.parent
    parent = node.parent
    index = 0
    siblings = 0
    parent_tag = ""
    if parent is not None:
        parent_tag = parent.tag
        for position, child in enumerate(parent.children):
            if child is node:
                index = position
        siblings = max(0, len(parent.children) - 1)
    tag = node.tag
    return {
        "tag": tag,
        "depth": depth,
        "parent_tag": parent_tag,
        "child_count": len(node.children),
        "sibling_count": siblings,
        "index_in_parent": index,
        "has_id": "id" in node.attrs,
        "class_count": len(node.classes),
        "is_heading": tag in {"h1", "h2", "h3", "h4", "h5", "h6"},
        "is_img": tag == "img",
        "is_link": tag == "a" or "href" in node.attrs,
        "is_semantic_tag": tag in SEMANTIC_TAGS,
    }


def _attrs_semantic(node: Node) -> dict:
    attrs = node.attrs
    return {
        "role": attrs.get("role", ""),
        "itemprop": attrs.get("itemprop", ""),
        "itemtype": attrs.get("itemtype", ""),
        "aria_label": attrs.get("aria-label", ""),
        "aria_labelledby": attrs.get("aria-labelledby", ""),
        "aria_valuenow": attrs.get("aria-valuenow", ""),
        "in_aria_hidden": "aria-hidden" in attrs,
        "data_attr_keys": tuple(
            sorted(key for key in attrs if key.startswith("data-"))
        ),
    }


def _attrs_content(node: Node) -> dict:
    attrs = node.attrs
    href = attrs.get("href", "")
    src = attrs.get("src", "")
    path_segments: tuple = ()
    query_keys: tuple = ()
    is_absolute = False
    if href:
        parts = urlsplit(href)
        path_segments = tuple(
            segment for segment in parts.path.split("/") if segment
        )
        query_keys = tuple(sorted(parse_qs(parts.query)))
        is_absolute = bool(parts.scheme or href.startswith("//"))
    src_extension = os.path.splitext(urlsplit(src).path)[1].lower() if src else ""
    return {
        "href": href,
        "href_path_segments": path_segments,
        "href_query_keys": query_keys,
        "href_is_absolute": is_absolute,
        "src": src,
        "src_extension": src_extension,
        "alt": attrs.get("alt", ""),
        "title_attr": attrs.get("title", ""),
        "value_attr": attrs.get("value", ""),
        "input_type": attrs.get("type", "") if node.tag == "input" else "",
        "placeholder_len": len(attrs.get("placeholder", "")),
    }


def _text_features(text: str) -> dict:
    text = text or ""
    length = len(text)
    tokens = _TOKEN_RE.findall(text)
    scripts: Counter = Counter()
    digit_count = letter_count = punct_count = symbol_count = 0
    for ch in text:
        if ch.isdigit():
            digit_count += 1
            scripts[_char_script(ch)] += 1
        elif ch.isalpha():
            letter_count += 1
            scripts[_char_script(ch)] += 1
        elif unicodedata.category(ch).startswith("P"):
            punct_count += 1
        elif unicodedata.category(ch).startswith("S"):
            symbol_count += 1
    dominant = scripts.most_common(1)[0][0] if scripts else "none"
    lowered = text.lower()
    has_currency = any(symbol in text for symbol in CURRENCY_SYMBOLS) or any(
        word in lowered for word in CURRENCY_WORDS
    )
    denom = length or 1
    return {
        "text_len": length,
        "token_count": len(tokens),
        "dominant_script": dominant,
        "script_count": len(scripts),
        "digit_script": _digit_script(text),
        "digit_count": digit_count,
        "letter_count": letter_count,
        "punct_count": punct_count,
        "symbol_count": symbol_count,
        "has_currency_symbol": has_currency,
        "digit_ratio": digit_count / denom,
        "letter_ratio": letter_count / denom,
        "punct_ratio": punct_count / denom,
        "symbol_ratio": symbol_count / denom,
    }


def _capture_features(node: Node) -> dict:
    raw = node.features or {}
    rect = raw.get("rect") or {}
    style = raw.get("style") or {}

    def number(value, default=0.0) -> float:
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return float(value)
        match = _NUMBER_RE.search(normalize_digits(str(value)))
        return float(match.group()) if match else default

    font_weight = number(style.get("fontWeight"))
    return {
        "rect_x": number(rect.get("x")),
        "rect_y": number(rect.get("y")),
        "rect_w": number(rect.get("w")),
        "rect_h": number(rect.get("h")),
        "offset_parent_null": bool(raw.get("offsetParentNull", False)),
        "scroll_width": number(raw.get("scrollWidth")),
        "client_width": number(raw.get("clientWidth")),
        "font_size": number(style.get("fontSize")),
        "font_weight": font_weight,
        "is_bold": bool(raw.get("isBold", font_weight >= 600)),
        "line_height": number(style.get("lineHeight")),
        "text_transform": str(style.get("textTransform") or ""),
        "letter_spacing": number(style.get("letterSpacing")),
        "color": str(style.get("color") or ""),
        "bg_color": str(style.get("bgColor") or ""),
        "cursor": str(style.get("cursor") or ""),
        "inner_text_len": number(raw.get("innerTextLen")),
        "is_truncated": bool(raw.get("isTruncated", False)),
    }


def node_features(
    node: Node,
    root: Optional[Node] = None,
    ever_visible: Optional[set[int]] = None,
    atype: str = "",
) -> dict:
    """All per-node features for ``node`` as one flat dictionary."""
    features: dict = {}
    features.update(_structural(node, root))
    features.update(_attrs_semantic(node))
    features.update(_attrs_content(node))
    features.update(_text_features(_subtree_text(node)))
    features["is_hidden_by_attrs"] = is_hidden_by_attrs(node.attrs)
    if ever_visible is None:
        features["ever_visible"] = not features["is_hidden_by_attrs"]
    else:
        features["ever_visible"] = node.id in ever_visible
    features["atype"] = atype
    features.update(_capture_features(node))
    return features


# ── page context ───────────────────────────────────────────────────────────

def page_features(raw: Mapping) -> dict:
    """Page-level features from a trace's ``meta`` and root ``<html>`` node."""
    meta = raw.get("meta") or {}
    viewport = meta.get("viewport") or {}
    html_attrs: dict = {}
    snapshot = raw.get("snapshot")
    node = snapshot.get("node") if isinstance(snapshot, Mapping) else snapshot
    if isinstance(node, Mapping):
        for child in node.get("childNodes") or []:
            if isinstance(child, Mapping) and str(
                child.get("tagName", "")
            ).lower() == "html":
                html_attrs = child.get("attributes") or {}
                break
    url = meta.get("page_url") or meta.get("url") or ""
    parts = urlsplit(url)
    return {
        "html_lang": meta.get("html_lang") or html_attrs.get("lang", ""),
        "html_dir": meta.get("html_dir") or html_attrs.get("dir", ""),
        "viewport_width": meta.get("viewport_width") or viewport.get("w", 0),
        "viewport_height": meta.get("viewport_height") or viewport.get("h", 0),
        "device_pixel_ratio": meta.get("device_pixel_ratio", 1),
        "page_url": url,
        "url_path_segments": tuple(
            segment for segment in parts.path.split("/") if segment
        ),
        "url_query_keys": tuple(sorted(parse_qs(parts.query))),
        "lang": meta.get("html_lang") or html_attrs.get("lang", ""),
        "dir": meta.get("html_dir") or html_attrs.get("dir", ""),
    }


# ── value-level type signature ─────────────────────────────────────────────

def value_types(value: str, atype: str = "") -> dict:
    """Boolean type signature for one cell value."""
    text = normalize_digits(value or "")
    lowered = text.lower().strip()
    number = parse_number(text)
    digits_only = re.sub(r"[\s()+-]", "", text)
    is_url = atype in {"href", "src"} or bool(_URL_RE.match(lowered))
    extension = os.path.splitext(urlsplit(lowered).path)[1]
    tokens = _TOKEN_RE.findall(text)
    return {
        "is_numeric": _is_numeric_value(text),
        "is_currency": any(symbol in text for symbol in CURRENCY_SYMBOLS)
        or any(word in lowered for word in CURRENCY_WORDS),
        "is_percent": any(marker in text for marker in PERCENT_MARKERS),
        "is_url": is_url,
        "is_image_path": extension in IMAGE_EXTS,
        "is_date_like": bool(_DATE_RE.search(text))
        or any(marker in text for marker in DATE_MARKERS),
        "is_phone_like": digits_only.isdigit()
        and 7 <= len(digits_only) <= 15,
        "is_short_text": len(text) <= 30,
        "is_free_text": len(text) > 40 and len(tokens) >= 5,
    }


def _is_numeric_value(text: str) -> bool:
    """True when ``text`` is essentially a number, not just contains digits.

    ``parse_number`` finds digits anywhere, so values like ``Core i3`` or
    ``N4500`` would otherwise be classified as numeric.  Strip known units and
    currency markers first, then require the remainder to be a plain number.
    """
    if not text or not any(ch.isdigit() for ch in text):
        return False
    stripped = text.lower()
    for token in (
        *CURRENCY_WORDS,
        *UNIT_TOKENS,
        *PERCENT_MARKERS,
        "inch",
        "in",
    ):
        stripped = stripped.replace(token.lower(), "")
    for symbol in CURRENCY_SYMBOLS:
        stripped = stripped.replace(symbol, "")
    stripped = re.sub(r"[\s,٬٫\u200c()+-]", "", stripped)
    return bool(re.fullmatch(r"\d+(?:\.\d+)?", stripped))


def _type_label(value: str, atype: str = "") -> str:
    flags = value_types(value, atype)
    for name in TYPE_FLAGS:
        if flags[name]:
            return name
    return "other"


# ── per-column features ────────────────────────────────────────────────────

def column_features(
    values: Sequence[str],
    node_feats: Optional[Sequence[dict]] = None,
    total_rows: Optional[int] = None,
    atype: str = "",
    positions: Optional[Sequence[int]] = None,
    sibling_positions: Optional[Sequence[Sequence[int]]] = None,
) -> dict:
    """Statistical, type, vocabulary and consistency features for a column."""
    values = [str(value) for value in values]
    node_feats = list(node_feats or [])
    total = total_rows if total_rows is not None else len(values)
    count = len(values)
    coverage = count / total if total else 0.0

    char_lengths = [len(value) for value in values]
    token_counts = [len(_TOKEN_RE.findall(value)) for value in values]
    numbers = [parse_number(value) for value in values]
    numeric = [number for number in numbers if number is not None]

    type_labels = Counter(_type_label(value, atype) for value in values)
    flag_counts = Counter()
    for value in values:
        for name, present in value_types(value, atype).items():
            if present:
                flag_counts[name] += 1
    signature = {
        name: (flag_counts[name] / count if count else 0.0) >= 0.6
        for name in TYPE_FLAGS
    }

    tokens: Counter = Counter()
    for value in values:
        tokens.update(token.lower() for token in _TOKEN_RE.findall(value))
    total_tokens = sum(tokens.values())
    unique_tokens = len(tokens)
    repeated_tokens = sum(1 for token, freq in tokens.items() if freq >= 2)
    free_text_tokens = sum(
        1 for token, freq in tokens.items() if freq == 1 and len(token) >= 4
    )

    lowered_values = [value.lower() for value in values]
    units = [
        unit for unit in UNIT_TOKENS
        if any(unit in value for value in lowered_values)
    ]
    unit_counter: Counter = Counter()
    for value in lowered_values:
        for unit in units:
            if unit in value:
                unit_counter[unit] += 1
    dominant_unit_count = unit_counter.most_common(1)[0][1] if unit_counter else 0
    unit_consistency = dominant_unit_count / count if count else 0.0

    numeric_with_affix = 0
    for value in values:
        if parse_number(value) is None:
            continue
        stripped = normalize_digits(value)
        stripped = re.sub(r"[-+]?\d+(?:\.\d+)?", "", stripped)
        if stripped.strip(" ,٬٫\u200c"):
            numeric_with_affix += 1
    numeric_prefix_suffix = numeric_with_affix / len(numeric) if numeric else 0.0

    common_prefix_len = 0
    if len(values) > 1:
        shortest = min(values, key=len)
        for index, ch in enumerate(shortest):
            if all(value[index] == ch for value in values):
                common_prefix_len = index + 1
            else:
                break
    mean_chars = _safe_mean(char_lengths)
    common_prefix_ratio = common_prefix_len / mean_chars if mean_chars else 0.0
    prefix_match_rate = (
        sum(1 for value in values if value.startswith(shortest[:common_prefix_len]))
        / count
        if count and common_prefix_len
        else 0.0
    )

    numeric_monotonic = 0.0
    if len(numeric) > 1:
        rising = sum(
            1 for left, right in zip(numeric, numeric[1:]) if right >= left
        )
        numeric_monotonic = rising / (len(numeric) - 1)

    position_stability = 0.5
    if node_feats:
        xs = [feat.get("rect_x", 0.0) for feat in node_feats]
        if any(xs):
            spread = _safe_stdev(xs)
            position_stability = 1.0 / (1.0 + spread / 100.0)

    mutex_rate = 0.0
    if positions is not None and sibling_positions:
        present = set(positions)
        conflicts = 0
        for sibling in sibling_positions:
            if present & set(sibling):
                conflicts += 1
        mutex_rate = conflicts / len(sibling_positions) if sibling_positions else 0.0

    return {
        "coverage": coverage,
        "null_rate": 1.0 - coverage,
        "cardinality": len(set(values)),
        "mean_char_len": mean_chars,
        "std_char_len": _safe_stdev(char_lengths),
        "mean_token_count": _safe_mean(token_counts),
        "std_token_count": _safe_stdev(token_counts),
        "entropy": _entropy(Counter(values)),
        "numeric_parsed_rate": len(numeric) / count if count else 0.0,
        "numeric_mean": _safe_mean(numeric),
        "numeric_std": _safe_stdev(numeric),
        "numeric_monotonic": numeric_monotonic,
        **signature,
        "type_entropy": _entropy(type_labels),
        "unique_token_ratio": unique_tokens / total_tokens if total_tokens else 0.0,
        "token_vocab_size": unique_tokens,
        "categorical_tokens": repeated_tokens,
        "free_text_tokens": free_text_tokens,
        "discovered_units": tuple(units),
        "unit_consistency": unit_consistency,
        "numeric_prefix_suffix": numeric_prefix_suffix,
        "common_prefix_len": common_prefix_ratio,
        "presence_consistency": coverage,
        "value_stability": 1.0 - (len(set(values)) / count if count else 0.0),
        "position_stability": position_stability,
        "neighbor_stability": 0.5,
        "mutex_rate": mutex_rate,
        "jaccard_with_other": 0.0,
        "prefix_match_rate": prefix_match_rate,
        "numeric_correlation": 0.0,
    }


# ── pairwise column features ───────────────────────────────────────────────

def _dominant(node_feats: Sequence[dict], key: str) -> str:
    values = [str(feat.get(key, "")) for feat in node_feats if feat.get(key)]
    if not values:
        return ""
    return Counter(values).most_common(1)[0][0]


def pairwise_features(left: Mapping, right: Mapping) -> dict:
    """All pairwise features for two column descriptors.

    A column descriptor is a mapping with ``features`` (from
    :func:`column_features`), optional ``node_feats``, ``atype``, ``positions``
    and ``neighbors``.
    """
    left_features = left.get("features") or {}
    right_features = right.get("features") or {}
    left_nodes = list(left.get("node_feats") or [])
    right_nodes = list(right.get("node_feats") or [])

    left_types = {name for name in TYPE_FLAGS if left_features.get(name)}
    right_types = {name for name in TYPE_FLAGS if right_features.get(name)}
    left_vector = np.array([1.0 if name in left_types else 0.0 for name in TYPE_FLAGS])
    right_vector = np.array([1.0 if name in right_types else 0.0 for name in TYPE_FLAGS])
    denom = float(np.linalg.norm(left_vector) * np.linalg.norm(right_vector))
    type_cosine = float(left_vector @ right_vector / denom) if denom else 0.0

    def mean_rect(nodes: Sequence[dict]) -> tuple[float, float]:
        if not nodes:
            return (0.0, 0.0)
        return (
            _safe_mean([feat.get("rect_x", 0.0) for feat in nodes]),
            _safe_mean([feat.get("rect_y", 0.0) for feat in nodes]),
        )

    left_rect, right_rect = mean_rect(left_nodes), mean_rect(right_nodes)
    layout_dist = math.dist(left_rect, right_rect)
    layout_dist = layout_dist / (1.0 + layout_dist)

    left_positions = set(left.get("positions") or ())
    right_positions = set(right.get("positions") or ())
    shared = left_positions & right_positions
    cooccurrence = len(shared) / len(left_positions | right_positions) if (
        left_positions or right_positions
    ) else 0.0

    return {
        "semantic_class_jaccard": _jaccard(left_types, right_types),
        "tag_match": float(
            bool(left_nodes and right_nodes)
            and _dominant(left_nodes, "tag") == _dominant(right_nodes, "tag")
        ),
        "atype_match": float(left.get("atype", "") == right.get("atype", "")),
        "itemprop_match": float(
            _dominant(left_nodes, "itemprop") == _dominant(right_nodes, "itemprop")
        ),
        "role_match": float(
            _dominant(left_nodes, "role") == _dominant(right_nodes, "role")
        ),
        "type_sig_cosine": type_cosine,
        "layout_dist": layout_dist,
        "neighbor_overlap": _jaccard(
            left.get("neighbors") or (), right.get("neighbors") or ()
        ),
        "discovered_unit_match": _jaccard(
            left_features.get("discovered_units") or (),
            right_features.get("discovered_units") or (),
        ),
        "mutex_clean": 1.0 - cooccurrence,
    }


# ── feature vectors ────────────────────────────────────────────────────────

_ATOM_NUMERIC = (
    "depth", "child_count", "sibling_count", "index_in_parent", "class_count",
    "text_len", "token_count", "script_count", "digit_count", "letter_count",
    "punct_count", "symbol_count", "digit_ratio", "letter_ratio",
    "punct_ratio", "symbol_ratio", "placeholder_len", "font_size",
    "font_weight", "line_height", "letter_spacing", "inner_text_len",
    "rect_w", "rect_h", "scroll_width", "client_width",
)
_ATOM_CATEGORICAL = (
    "tag", "parent_tag", "atype", "role", "itemprop", "itemtype",
    "dominant_script", "digit_script", "text_transform", "cursor",
    "input_type", "src_extension", "href_query_keys", "discovered_units",
)
_ATOM_BOOLEAN = (
    "has_id", "is_heading", "is_img", "is_link", "is_semantic_tag",
    "in_aria_hidden", "href_is_absolute", "has_currency_symbol",
    "is_hidden_by_attrs", "ever_visible", "is_bold", "is_truncated",
    "offset_parent_null", "is_numeric", "is_currency", "is_percent", "is_url",
    "is_image_path", "is_date_like", "is_phone_like", "is_short_text",
    "is_free_text",
)

_ATOM_WEIGHTS: dict[str, float] = {
    # semantic — high
    **{k: 2.0 for k in (
        "atype", "tag", "itemprop", "role", "itemtype",
        "input_type", "src_extension",
        "is_numeric", "is_currency", "is_percent", "is_url",
        "is_image_path", "is_date_like", "is_phone_like",
        "is_short_text", "is_free_text",
    )},
    # type — medium
    **{k: 1.5 for k in (
        "dominant_script", "digit_script", "has_currency_symbol",
    )},
    # content
    **{k: 1.0 for k in (
        "text_len", "token_count", "href_query_keys",
        "href_path_segments", "discovered_units",
    )},
    # structural — low
    **{k: 0.3 for k in (
        "depth", "index_in_parent", "sibling_count",
        "parent_tag", "rect_x", "rect_y",
    )},
}

_ROW_NUMERIC = (
    "depth", "child_count", "class_count", "text_len", "token_count",
    "rect_w", "rect_h", "font_size", "font_weight", "inner_text_len",
    "scroll_width", "client_width",
)
_ROW_CATEGORICAL = (
    "tag", "parent_tag", "role", "itemprop", "dominant_script",
    "text_transform",
)
_ROW_BOOLEAN = (
    "has_id", "is_heading", "is_img", "is_link", "is_semantic_tag",
    "is_bold", "is_truncated",
)


def _hashable(value) -> str:
    if isinstance(value, (list, tuple)):
        return "|".join(str(item) for item in value)
    return "" if value is None else str(value)


class FeatureVectorizer:
    """Fit a numeric vectorizer over feature dictionaries.

    Numeric features are standardized; categorical features are one-hot
    encoded against the vocabulary seen during :meth:`fit`; booleans are 0/1.
    Every feature in the selected group contributes to the vector.
    """

    def __init__(
        self, kind: str = "atom", weights: Optional[dict[str, float]] = None
    ) -> None:
        if kind == "atom":
            self.numeric, self.categorical, self.boolean = (
                _ATOM_NUMERIC, _ATOM_CATEGORICAL, _ATOM_BOOLEAN,
            )
        elif kind == "row":
            self.numeric, self.categorical, self.boolean = (
                _ROW_NUMERIC, _ROW_CATEGORICAL, _ROW_BOOLEAN,
            )
        else:
            raise ValueError(f"unknown feature kind: {kind}")
        self.kind = kind
        self.weights = weights or {}
        self._vocab: dict[str, list[str]] = {}
        self._mean: dict[str, float] = {}
        self._std: dict[str, float] = {}

    def fit(self, items: Sequence[Mapping]) -> "FeatureVectorizer":
        for key in self.numeric:
            values = [float(item.get(key) or 0.0) for item in items]
            self._mean[key] = _safe_mean(values)
            self._std[key] = _safe_stdev(values) or 1.0
        for key in self.categorical:
            vocab = sorted({_hashable(item.get(key)) for item in items})
            self._vocab[key] = vocab
        return self

    def transform_one(self, item: Mapping) -> np.ndarray:
        vector: list[float] = []
        for key in self.numeric:
            weight = self.weights.get(key, 1.0)
            value = float(item.get(key) or 0.0)
            vector.append(
                weight
                * (value - self._mean.get(key, 0.0))
                / self._std.get(key, 1.0)
            )
        for key in self.boolean:
            weight = self.weights.get(key, 1.0)
            vector.append(weight * (1.0 if item.get(key) else 0.0))
        for key in self.categorical:
            weight = self.weights.get(key, 1.0)
            value = _hashable(item.get(key))
            vocab = self._vocab.get(key) or [value]
            vector.extend(
                weight * (1.0 if value == candidate else 0.0)
                for candidate in vocab
            )
        return np.asarray(vector, dtype=np.float64)

    def transform(self, items: Sequence[Mapping]) -> np.ndarray:
        if not items:
            return np.zeros((0, 0), dtype=np.float64)
        return np.vstack([self.transform_one(item) for item in items])

    def fit_transform(self, items: Sequence[Mapping]) -> np.ndarray:
        return self.fit(items).transform(items)


def cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
    denom = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denom == 0.0:
        return 0.0
    return float(left @ right / denom)


def feature_similarity(left: Mapping, right: Mapping, kind: str = "atom") -> float:
    """Cosine similarity of two feature dictionaries in a shared space."""
    vectorizer = FeatureVectorizer(kind).fit([left, right])
    vectors = vectorizer.transform([left, right])
    return cosine_similarity(vectors[0], vectors[1])


def similarity_matrix(items: Sequence[Mapping], kind: str = "atom") -> np.ndarray:
    """Pairwise cosine similarity matrix for a batch of feature dictionaries."""
    if len(items) < 2:
        return np.ones((len(items), len(items)), dtype=np.float64)
    vectors = FeatureVectorizer(kind).fit_transform(items)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    unit = vectors / norms
    return unit @ unit.T


__all__ = [
    "NODE_STRUCTURAL", "NODE_ATTR_SEMANTIC", "NODE_ATTR_CONTENT", "NODE_TEXT",
    "NODE_VISIBILITY", "NODE_ATOM", "NODE_CAPTURE", "PAGE_FEATURES",
    "COLUMN_FEATURES", "PAIRWISE_FEATURES", "TYPE_FLAGS",
    "FeatureVectorizer", "column_features", "cosine_similarity",
    "feature_similarity", "node_features", "normalize_digits", "page_features",
    "pairwise_features", "parse_number", "similarity_matrix", "value_types",
]
