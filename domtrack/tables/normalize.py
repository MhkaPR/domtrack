"""Normalize heterogeneous tables into single-concept columns.

The input is any table whose columns may mix unrelated concepts.  Every cell is
embedded *with its source column identity* so that identical values in different
columns do not collapse onto the same vector.  Cells are then clustered inside
each source column with agglomerative hierarchical clustering (cosine distance,
average linkage).  Suspicious clusters are recursively split, tiny clusters are
marked ``rare``, and the final columns are optionally merged when two split
columns turn out to represent the same concept.

The public entry point is :func:`normalize_table`.  :class:`CellEmbedder` can be
replaced or injected, which keeps the pipeline testable without downloading a
model.
"""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Mapping, Optional, Sequence

import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics.pairwise import cosine_distances
from sklearn.preprocessing import normalize

try:
    from sentence_transformers import SentenceTransformer
except ImportError:  # pragma: no cover - reported when a model is requested
    SentenceTransformer = None

try:
    import torch
except ImportError:  # pragma: no cover - CPU-only environments
    torch = None


DEFAULT_MODEL_NAME = "intfloat/multilingual-e5-small"
DEFAULT_CONTEXT_TEMPLATE = "query: {column} | {value}"
URL_ATYPES = frozenset({"href", "src"})


# ── configuration ──────────────────────────────────────────────────────────


@dataclass
class NormalizeConfig:
    """Tuning knobs for :func:`normalize_table`.

    ``distance_threshold`` is deliberately strict: separating related values
    such as two city names is harmless, while merging unrelated values is not.
    """

    model_name: str = DEFAULT_MODEL_NAME
    context_template: str = DEFAULT_CONTEXT_TEMPLATE
    batch_size: int = 64
    device: Optional[str] = None
    half_precision: bool = True
    max_workers: Optional[int] = None

    distance_threshold: float = 0.04
    homogeneity_threshold: float = 0.04
    max_auto_threshold: float = 0.15
    min_centroid_separation_ratio: float = 1.2
    free_text_min_avg_chars: int = 30
    free_text_min_unique_ratio: float = 0.8
    recursive_threshold_scale: float = 0.7
    max_split_depth: int = 4
    min_cluster_size: int = 3
    max_clusters_per_column: int = 10

    merge_related: bool = True
    merge_within_source: bool = False

    min_coverage: float = 0.6

    def __post_init__(self) -> None:
        if not 0.0 < self.distance_threshold <= 2.0:
            raise ValueError("distance_threshold must be in (0, 2]")
        if self.homogeneity_threshold < 0.0:
            raise ValueError("homogeneity_threshold must be >= 0")
        if not 0.0 < self.recursive_threshold_scale < 1.0:
            raise ValueError("recursive_threshold_scale must be in (0, 1)")
        if self.min_cluster_size < 1:
            raise ValueError("min_cluster_size must be >= 1")
        if self.batch_size < 1:
            raise ValueError("batch_size must be >= 1")


# ── embedding ──────────────────────────────────────────────────────────────


class CellEmbedder:
    """Embed cells with their column context, in parallel across columns.

    ``model`` may be any object with an ``encode`` method.  This is the main
    testing seam: pass a small fake model and no download is needed.
    """

    def __init__(
        self,
        model=None,
        model_name: Optional[str] = None,
        context_template: str = DEFAULT_CONTEXT_TEMPLATE,
        batch_size: int = 64,
        device: Optional[str] = None,
        half_precision: bool = True,
    ) -> None:
        self._model = model
        self.model_name = model_name or DEFAULT_MODEL_NAME
        self.context_template = context_template
        self.batch_size = max(1, batch_size)
        self.device = device
        self.half_precision = half_precision

    @property
    def model(self):
        if self._model is None:
            self._model = self._load_model()
        return self._model

    def _load_model(self):
        if SentenceTransformer is None:
            raise RuntimeError(
                "sentence-transformers is required for table normalization; "
                "install it with `pip install sentence-transformers`"
            )

        kwargs = {}
        if self.device:
            kwargs["device"] = self.device
        model = SentenceTransformer(self.model_name, **kwargs)
        if self.half_precision and torch is not None and torch.cuda.is_available():
            model = model.half()
        return model

    def text_for(self, column: str, value: str) -> str:
        """The exact text sent to the model; column identity is always present."""
        return self.context_template.format(column=column, value=value)

    def encode_texts(self, texts: Sequence[str]):
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)

        kwargs = {
            "batch_size": self.batch_size,
            "convert_to_numpy": True,
            "normalize_embeddings": True,
            "show_progress_bar": False,
        }
        if self.device:
            kwargs["device"] = self.device

        try:
            vectors = self.model.encode(list(texts), **kwargs)
        except TypeError:
            try:
                vectors = self.model.encode(
                    list(texts),
                    convert_to_numpy=True,
                    normalize_embeddings=True,
                )
            except TypeError:
                vectors = self.model.encode(list(texts))
        except Exception:
            # Some fake/lightweight models only implement the minimal call.
            vectors = self.model.encode(list(texts))

        vectors = np.asarray(vectors, dtype=np.float32)
        if vectors.ndim == 1:
            vectors = vectors.reshape(1, -1)
        return normalize(vectors, norm="l2")

    def encode_column(
        self, column: str, values: Sequence[str], context: Optional[str] = None
    ):
        texts = [self.text_for(context or column, value) for value in values]
        return self.encode_texts(texts)

    def embed_columns(
        self,
        columns: Mapping[str, Sequence[str]],
        max_workers: Optional[int] = None,
        contexts: Optional[Mapping[str, str]] = None,
    ) -> dict[str, object]:
        """Embed several columns concurrently; results keep input order."""
        contexts = contexts or {}
        if not columns:
            return {}
        if len(columns) == 1 or max_workers == 1:
            return {
                name: self.encode_column(name, values, contexts.get(name))
                for name, values in columns.items()
            }

        workers = max_workers or min(4, len(columns))
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {
                name: pool.submit(
                    self.encode_column, name, values, contexts.get(name)
                )
                for name, values in columns.items()
            }
            return {name: future.result() for name, future in futures.items()}


# ── clustering ─────────────────────────────────────────────────────────────


def _cluster_spread(vectors, indices: Sequence[int]) -> float:
    """Mean cosine distance of members from their centroid."""
    if len(indices) <= 1:
        return 0.0
    members = np.asarray(vectors)[list(indices)]
    centroid = members.mean(axis=0, keepdims=True)
    return float(cosine_distances(members, centroid).mean())


def _agglomerative(vectors, threshold: float) -> list[list[int]]:
    """Cluster normalized vectors with average-linkage cosine agglomeration."""
    n = len(vectors)
    if n <= 1:
        return [list(range(n))]

    try:
        model = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=float(threshold),
            metric="cosine",
            linkage="average",
        )
        labels = model.fit_predict(np.asarray(vectors))
    except TypeError:
        model = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=float(threshold),
            affinity="cosine",
            linkage="average",
        )
        labels = model.fit_predict(np.asarray(vectors))

    groups: dict[int, list[int]] = defaultdict(list)
    for index, label in enumerate(labels):
        groups[int(label)].append(index)
    return list(groups.values())


def _force_two_way_split(vectors, indices: Sequence[int]) -> list[list[int]]:
    if len(indices) < 2:
        return [list(indices)]
    matrix = np.asarray(vectors)[list(indices)]
    try:
        model = AgglomerativeClustering(
            n_clusters=2, metric="cosine", linkage="average"
        )
        labels = model.fit_predict(matrix)
    except TypeError:
        model = AgglomerativeClustering(
            n_clusters=2, affinity="cosine", linkage="average"
        )
        labels = model.fit_predict(matrix)
    groups: dict[int, list[int]] = defaultdict(list)
    for position, label in enumerate(labels):
        groups[int(label)].append(int(indices[position]))
    return list(groups.values())


def _refine_cluster(
    vectors,
    indices: Sequence[int],
    config: NormalizeConfig,
    threshold: float,
    depth: int,
) -> list[list[int]]:
    indices = list(indices)
    if len(indices) <= 1 or depth >= config.max_split_depth:
        return [indices]

    spread = _cluster_spread(vectors, indices)
    if spread <= config.homogeneity_threshold:
        return [indices]

    next_threshold = max(1e-6, threshold * config.recursive_threshold_scale)
    local = [vectors[i] for i in indices]
    parts = _agglomerative(local, next_threshold)
    parts = [[indices[i] for i in part] for part in parts]

    if len(parts) == 1 and len(indices) >= 2 * config.min_cluster_size:
        forced = _force_two_way_split(vectors, indices)
        if len(forced) > 1:
            parts = forced

    if len(parts) == 1:
        return [indices]

    refined: list[list[int]] = []
    for part in parts:
        refined.extend(
            _refine_cluster(vectors, part, config, next_threshold, depth + 1)
        )
    return refined


def cluster_vectors(vectors, config: Optional[NormalizeConfig] = None) -> list[list[int]]:
    """Return clusters of row positions for one column's normalized vectors."""
    config = config or NormalizeConfig()
    n = len(vectors)
    if n == 0:
        return []
    if n == 1:
        return [[0]]

    initial = _agglomerative(vectors, config.distance_threshold)
    clusters: list[list[int]] = []
    for cluster in initial:
        clusters.extend(
            _refine_cluster(vectors, cluster, config, config.distance_threshold, 0)
        )

    clusters.sort(key=lambda cluster: (-len(cluster), min(cluster)))
    return clusters


def _cluster_with_cap(vectors, config: NormalizeConfig) -> list[list[int]]:
    """Cluster one column, raising thresholds if it would explode."""
    threshold = config.distance_threshold
    homogeneity = config.homogeneity_threshold
    clusters = cluster_vectors(vectors, config)
    for _ in range(8):
        if len(clusters) <= config.max_clusters_per_column:
            break
        threshold *= 1.35
        homogeneity *= 1.35
        clusters = cluster_vectors(
            vectors,
            replace(
                config,
                distance_threshold=min(threshold, 2.0),
                homogeneity_threshold=homogeneity,
            ),
        )
    return clusters


def _auto_distance_threshold(vectors, config: NormalizeConfig) -> float:
    """Pick a per-column threshold at the clearest gap in its distances."""
    if len(vectors) < 3:
        return config.distance_threshold
    distances = cosine_distances(vectors)
    upper = distances[np.triu_indices(len(vectors), 1)]
    if not len(upper):
        return config.distance_threshold
    ordered = np.sort(upper)
    gaps = np.diff(ordered)
    if not len(gaps):
        return config.distance_threshold
    index = int(np.argmax(gaps))
    if gaps[index] < 0.005:
        return config.distance_threshold
    candidate = float((ordered[index] + ordered[index + 1]) / 2.0)
    return min(
        max(candidate, config.distance_threshold),
        config.max_auto_threshold,
    )


def _should_split(
    clusters: list[list[int]],
    total: int,
    unique_ratio: float,
    config: NormalizeConfig,
    vectors=None,
    avg_chars: float = 0.0,
) -> bool:
    """Whether the evidence supports more than one concept in a column."""
    if len(clusters) <= 1 or total == 0:
        return False
    if (
        unique_ratio >= config.free_text_min_unique_ratio
        and avg_chars >= config.free_text_min_avg_chars
    ):
        return False
    sizes = sorted((len(cluster) for cluster in clusters), reverse=True)
    if sizes[0] / total >= 0.9:
        return False
    if sizes[0] / total >= 0.8:
        return True
    meaningful = [size for size in sizes if size >= config.min_cluster_size]
    if len(meaningful) < 2:
        return False
    second = sizes[1]
    minimum_second = max(config.min_cluster_size, int(0.2 * total))
    if unique_ratio >= 0.8:
        minimum_second = max(minimum_second, int(0.25 * total))
    if second < minimum_second:
        return False
    if vectors is not None:
        ranked = sorted(range(len(clusters)), key=lambda i: -len(clusters[i]))
        left = list(clusters[ranked[0]])
        right = list(clusters[ranked[1]])
        left_centroid = np.asarray(vectors)[left].mean(axis=0, keepdims=True)
        right_centroid = np.asarray(vectors)[right].mean(axis=0, keepdims=True)
        centroid_distance = float(
            cosine_distances(left_centroid, right_centroid)[0, 0]
        )
        left_spread = _cluster_spread(vectors, left)
        right_spread = _cluster_spread(vectors, right)
        if centroid_distance < (
            config.min_centroid_separation_ratio
            * (left_spread + right_spread)
        ):
            return False
    return True


def _discover_column_clusters(
    vectors, values: Sequence[str], config: NormalizeConfig
) -> tuple[list[list[int]], set[int]]:
    """Cluster one text column, relaxing thresholds until coverage is healthy."""
    total = len(values)
    unique_ratio = len(set(values)) / total if total else 0.0
    avg_chars = (
        sum(len(value) for value in values) / total if total else 0.0
    )
    threshold = _auto_distance_threshold(vectors, config)
    homogeneity = max(config.homogeneity_threshold, threshold * 0.75)

    for _ in range(8):
        local = replace(
            config,
            distance_threshold=min(threshold, 2.0),
            homogeneity_threshold=homogeneity,
        )
        clusters = _cluster_with_cap(vectors, local)
        if _should_split(
            clusters, total, unique_ratio, local, vectors, avg_chars
        ):
            clean = sum(
                len(cluster)
                for cluster in clusters
                if len(cluster) >= config.min_cluster_size
            )
            if clean / total >= config.min_coverage:
                return _absorb_rare_clusters(vectors, clusters, config)
        if threshold >= config.max_auto_threshold:
            break
        threshold *= 1.35
        homogeneity *= 1.35

    return [list(range(total))], set()


def _absorb_rare_clusters(
    vectors, clusters: list[list[int]], config: NormalizeConfig
) -> tuple[list[list[int]], set[int]]:
    """Fold tiny clusters into the concept they most likely belong to.

    A rare cluster is absorbed only when the evidence is unambiguous: with
    several clean concepts, the nearest one must be separated from the second
    nearest by ``min_centroid_separation_ratio``; with a single clean concept,
    the rare value must lie inside that concept's own radius.  Otherwise the
    cluster stays rare, so genuinely unrelated values are never swallowed.
    """
    if len(clusters) <= 1:
        return clusters, set()
    clean = [list(cluster) for cluster in clusters if len(cluster) >= config.min_cluster_size]
    rare = [list(cluster) for cluster in clusters if len(cluster) < config.min_cluster_size]
    if not clean or not rare:
        return clusters, set()

    members = np.asarray(vectors)
    centroids = []
    radii = []
    for cluster in clean:
        centroid = normalize(
            members[cluster].mean(axis=0, keepdims=True), norm="l2"
        )[0]
        centroids.append(centroid)
        radii.append(
            float(
                cosine_distances(
                    members[cluster], centroid.reshape(1, -1)
                ).max()
            )
        )
    centroids = np.asarray(centroids)

    result = [list(cluster) for cluster in clean]
    absorbed: set[int] = set()
    for cluster in rare:
        centroid = normalize(
            members[cluster].mean(axis=0, keepdims=True), norm="l2"
        )[0]
        distances = cosine_distances(centroid.reshape(1, -1), centroids)[0]
        order = np.argsort(distances)
        nearest = int(order[0])
        if len(order) == 1:
            absorb = distances[nearest] <= radii[nearest]
        else:
            absorb = distances[order[1]] >= (
                config.min_centroid_separation_ratio * distances[nearest]
            )
        if absorb:
            result[nearest].extend(cluster)
            absorbed.update(cluster)
        else:
            result.append(cluster)
    result.sort(key=lambda cluster: (-len(cluster), min(cluster)))
    return result, absorbed


# ── table input / output helpers ───────────────────────────────────────────


@dataclass
class SourceColumn:
    id: str
    label: str
    semantic_label: Optional[str] = None
    values: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


def _cell_value(row, column_id: str, position: int) -> str:
    if isinstance(row, Mapping):
        value = row.get(column_id, "")
    elif isinstance(row, Sequence) and not isinstance(row, (str, bytes)):
        value = row[position] if position < len(row) else ""
    else:
        value = ""
    return "" if value is None else str(value)


def _source_columns(table) -> list[SourceColumn]:
    if isinstance(table, list):
        table = {"columns": list(table[0].keys()) if table else [], "rows": table}
    if not isinstance(table, Mapping):
        raise TypeError("table must be a mapping or a list of row mappings")

    rows = list(table.get("rows") or [])
    columns = table.get("columns")
    if columns is None:
        columns = table.get("headers")

    if columns is None:
        if rows and isinstance(rows[0], Mapping):
            columns = [
                key
                for key in rows[0].keys()
                if key not in {"_extra", "_row_index", "row_index"}
            ]
        else:
            columns = []

    sources: list[SourceColumn] = []
    for position, column in enumerate(columns):
        if isinstance(column, Mapping):
            column_id = str(column.get("id") or column.get("name") or position)
            label = str(
                column.get("label") or column_id
            )
            semantic_label = column.get("semantic_label")
            raw = dict(column)
        else:
            column_id = str(column)
            label = column_id
            semantic_label = None
            raw = {}
        values = [_cell_value(row, column_id, position) for row in rows]
        sources.append(
            SourceColumn(
                column_id,
                label,
                str(semantic_label) if semantic_label else None,
                values,
                raw,
            )
        )
    return sources


def neutral_column_name(
    source_column: str, cluster_index: int, rare: bool = False
) -> str:
    """A neutral, traceable name for one discovered concept column."""
    if rare:
        return f"{source_column}__rare_c{cluster_index}"
    return f"{source_column}__c{cluster_index}"


def _is_blank(value: str) -> bool:
    return not value.strip()


# ── normalization ──────────────────────────────────────────────────────────


def normalize_table(
    table,
    config: Optional[NormalizeConfig] = None,
    embedder: Optional[CellEmbedder] = None,
) -> dict:
    """Split a heterogeneous table into one-concept output columns.

    The returned table keeps the original row order and adds ``_row_index`` to
    every row.  Empty cells mean the row did not contain that concept.
    """
    config = config or NormalizeConfig()
    sources = _source_columns(table)
    row_count = max((len(source.values) for source in sources), default=0)

    embedder = embedder or CellEmbedder(
        model_name=config.model_name,
        context_template=config.context_template,
        batch_size=config.batch_size,
        device=config.device,
        half_precision=config.half_precision,
    )

    # Embed only non-empty cells, but remember their original row positions.
    nonempty_rows: dict[str, list[int]] = {}
    texts_by_column: dict[str, list[str]] = {}
    contexts: dict[str, str] = {}
    for position, source in enumerate(sources):
        positions = [
            index for index, value in enumerate(source.values) if not _is_blank(value)
        ]
        nonempty_rows[source.id] = positions
        if source.raw.get("atype") not in URL_ATYPES:
            texts_by_column[source.id] = [
                source.values[index] for index in positions
            ]
        # A short unique token preserves the column boundary without letting a
        # long DOM path dominate the cell's meaning.
        contexts[source.id] = f"c{position}"

    vectors_by_column = embedder.embed_columns(
        texts_by_column, max_workers=config.max_workers, contexts=contexts
    )

    # Discover concepts inside each source column.
    discovered: list[dict] = []
    report_sources: list[dict] = []

    for source in sources:
        positions = nonempty_rows[source.id]
        vectors = vectors_by_column.get(source.id)
        if not positions:
            clusters = []
            absorbed: set[int] = set()
        elif source.raw.get("atype") in URL_ATYPES:
            # URLs/paths are already one structural concept; clustering them by
            # meaning would shatter a perfectly homogeneous column.
            clusters = [list(range(len(positions)))]
            absorbed = set()
        else:
            values = [source.values[index] for index in positions]
            clusters, absorbed = _discover_column_clusters(
                vectors, values, config
            )

        split = len(clusters) > 1
        total_cells = len(positions)
        source_clusters: list[dict] = []
        clean_cells = 0

        for cluster_index, cluster in enumerate(clusters):
            rare = split and len(cluster) < config.min_cluster_size
            if not rare:
                clean_cells += len(cluster)

            if split:
                column_id = neutral_column_name(
                    source.id, cluster_index, rare=rare
                )
            else:
                column_id = source.id

            row_positions = [positions[position] for position in cluster]
            homogeneity = (
                _cluster_spread(vectors, cluster) if vectors is not None else 0.0
            )
            coverage = len(cluster) / total_cells if total_cells else 0.0

            column = {
                "id": column_id,
                "label": column_id,
                "atype": source.raw.get("atype", ""),
                "path": source.raw.get("path", ""),
                "source_column": source.id,
                "source_label": source.label,
                "cluster": cluster_index,
                "split": split,
                "rare": rare,
                "size": len(cluster),
                "coverage": round(coverage, 4),
                "homogeneity": round(homogeneity, 4),
                "semantic_label": source.semantic_label,
                "_rows": row_positions,
                "_vector_positions": [
                    position
                    for position in cluster
                    if position not in absorbed
                ],
            }
            discovered.append(column)
            source_clusters.append(
                {
                    "column": column_id,
                    "cluster": cluster_index,
                    "size": len(cluster),
                    "coverage": round(coverage, 4),
                    "homogeneity": round(homogeneity, 4),
                    "rare": rare,
                }
            )

        report_sources.append(
            {
                "source_column": source.id,
                "source_label": source.label,
                "total_cells": total_cells,
                "concepts": len(clusters),
                "clean_concepts": sum(
                    1 for item in source_clusters if not item["rare"]
                ),
                "rare_concepts": sum(
                    1 for item in source_clusters if item["rare"]
                ),
                "coverage": round(clean_cells / total_cells, 4)
                if total_cells
                else 1.0,
                "clusters": source_clusters,
            }
        )

    merged_columns: list[dict] = []
    if config.merge_related and len(discovered) > 1:
        merge_sources = {
            column["source_column"]
            for column in discovered
            if not column.get("rare")
        }
        merge_texts = {}
        for source in sources:
            if source.id not in merge_sources:
                continue
            merge_texts[source.id] = [
                source.values[index] for index in nonempty_rows[source.id]
            ]
        merge_contexts = {source_id: "value" for source_id in merge_texts}
        merge_vectors = embedder.embed_columns(
            merge_texts,
            max_workers=config.max_workers,
            contexts=merge_contexts,
        )
        discovered, merged_columns = _merge_related_columns(
            discovered, merge_vectors, config
        )

    # Build output rows.  Every cell starts empty and is moved to its concept.
    output_columns: list[dict] = []
    for column in discovered:
        public = {
            key: value
            for key, value in column.items()
            if not key.startswith("_")
        }
        output_columns.append(public)

    output_rows: list[dict] = []
    for row_index in range(row_count):
        output_rows.append({"_row_index": row_index})

    for column in discovered:
        column_id = column["id"]
        for row_index in column["_rows"]:
            if row_index >= row_count:
                continue
            value = _value_at_row(
                column, row_index, sources, nonempty_rows
            )
            if value == "":
                continue
            current = output_rows[row_index].get(column_id, "")
            output_rows[row_index][column_id] = (
                f"{current} | {value}" if current else value
            )

    for row in output_rows:
        for column in output_columns:
            row.setdefault(column["id"], "")

    total_cells = sum(item["total_cells"] for item in report_sources)
    clean_cells = sum(
        item["total_cells"] * item["coverage"] for item in report_sources
    )
    report = {
        "input_columns": len(sources),
        "output_columns": len(output_columns),
        "rows": row_count,
        "total_cells": total_cells,
        "coverage": round(clean_cells / total_cells, 4) if total_cells else 1.0,
        "source_columns": report_sources,
        "merged_columns": merged_columns,
    }

    base_meta = dict(table.get("meta") or {}) if isinstance(table, Mapping) else {}
    base_meta.update(
        {
            "normalized": True,
            "input_columns": len(sources),
            "output_columns": len(output_columns),
            "row_count": row_count,
            "columns": len(output_columns),
            "row_index_field": "_row_index",
            "distance_threshold": config.distance_threshold,
            "homogeneity_threshold": config.homogeneity_threshold,
            "min_cluster_size": config.min_cluster_size,
        }
    )
    result = {
        "meta": base_meta,
        "columns": output_columns,
        "rows": output_rows,
        "report": report,
    }
    result["validation"] = validate_normalization(result, config)
    return result


def _value_at_row(
    column: dict,
    row_index: int,
    sources: list[SourceColumn],
    nonempty_rows: dict[str, list[int]],
) -> str:
    """Recover the cell value for a discovered column at one row."""
    source_by_id = {source.id: source for source in sources}

    if column.get("merged_from"):
        parts = []
        for member in column["merged_from"]:
            if row_index not in member.get("rows", ()):
                continue
            source = source_by_id.get(member["source_column"])
            if source is None or row_index >= len(source.values):
                continue
            value = source.values[row_index]
            if value and not _is_blank(value) and value not in parts:
                parts.append(value)
        return " | ".join(parts)

    source = source_by_id.get(column["source_column"])
    if source is None or row_index >= len(source.values):
        return ""
    return source.values[row_index]


def _merge_related_columns(
    columns: list[dict],
    vectors_by_column: dict,
    config: NormalizeConfig,
) -> tuple[list[dict], list[dict]]:
    """Union split columns that are not distinguishable in semantic space.

    For each candidate pair we compare the distance between their centroids
    with the radius (mean distance to centroid) of the tighter cluster.  The
    pair is one concept when each centroid lies inside the other cluster's
    radius, i.e. ``centroid_distance <= min(spread_left, spread_right)``.
    This is scale-free: it uses only the geometry of the pair, so no absolute
    cosine cutoff is tuned to any particular page.  Groups are formed with
    complete linkage, so a column can never be pulled into a concept through a
    chain of pairwise coincidences.
    """
    candidates = [
        column
        for column in columns
        if not column.get("rare")
    ]
    if len(candidates) < 2:
        return columns, []

    centroids: dict[str, object] = {}
    spreads: dict[str, float] = {}
    for column in candidates:
        vectors = vectors_by_column.get(column["source_column"])
        positions = column.get("_vector_positions") or []
        if vectors is None or not len(positions):
            continue
        members = np.asarray(vectors)[positions]
        centroid = normalize(members.mean(axis=0, keepdims=True), norm="l2")[0]
        centroids[column["id"]] = centroid
        spreads[column["id"]] = float(
            cosine_distances(members, centroid.reshape(1, -1)).mean()
        )

    margins: dict[tuple[str, str], float] = {}
    distances: dict[tuple[str, str], float] = {}
    for i, left in enumerate(candidates):
        for right in candidates[i + 1:]:
            if not config.merge_within_source and (
                left["source_column"] == right["source_column"]
            ):
                continue
            left_url = left.get("atype") in URL_ATYPES
            right_url = right.get("atype") in URL_ATYPES
            if left_url != right_url:
                continue
            if left_url and left.get("atype") != right.get("atype"):
                continue
            if left["id"] not in centroids or right["id"] not in centroids:
                continue
            distance = float(
                cosine_distances(
                    centroids[left["id"]].reshape(1, -1),
                    centroids[right["id"]].reshape(1, -1),
                )[0, 0]
            )
            radius = min(spreads[left["id"]], spreads[right["id"]])
            distances[(left["id"], right["id"])] = distance
            margins[(left["id"], right["id"])] = radius - distance

    def pair_score(left: dict, right: dict) -> Optional[float]:
        key = (left["id"], right["id"])
        if key in margins:
            return margins[key]
        return margins.get((right["id"], left["id"]))

    def linkage(left_group: list[dict], right_group: list[dict]) -> Optional[float]:
        """Complete linkage: the least overlapping cross-pair decides."""
        worst: Optional[float] = None
        for left in left_group:
            for right in right_group:
                margin = pair_score(left, right)
                if margin is None:
                    return None
                if worst is None or margin < worst:
                    worst = margin
        return worst

    groups: list[list[dict]] = [[column] for column in candidates]
    while True:
        best: Optional[float] = None
        best_pair: Optional[tuple[int, int]] = None
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                margin = linkage(groups[i], groups[j])
                if margin is None or margin < 0.0:
                    continue
                if best is None or margin > best:
                    best = margin
                    best_pair = (i, j)
        if best_pair is None:
            break
        i, j = best_pair
        groups[i] = groups[i] + groups[j]
        del groups[j]

    merged_groups = [members for members in groups if len(members) > 1]
    if not merged_groups:
        return columns, []

    group_of: dict[str, list[dict]] = {}
    for members in merged_groups:
        for member in members:
            group_of[member["id"]] = members

    merged_ids: dict[str, str] = {}
    merged_report: list[dict] = []
    for members in merged_groups:
        member_ids = [member["id"] for member in members]
        merged_id = "merged__" + "+".join(member_ids)
        for member in members:
            merged_ids[member["id"]] = merged_id
        pair_distances = [
            distance
            for (a, b), distance in distances.items()
            if a in member_ids and b in member_ids
        ]
        merged_report.append(
            {
                "id": merged_id,
                "from": member_ids,
                "sources": sorted({m["source_column"] for m in members}),
                "max_distance": round(max(pair_distances), 4)
                if pair_distances
                else 0.0,
            }
        )

    output: list[dict] = []
    emitted: set[str] = set()
    for column in columns:
        merged_id = merged_ids.get(column["id"])
        if merged_id is None:
            output.append(column)
            continue
        if merged_id in emitted:
            continue
        emitted.add(merged_id)
        members = group_of[column["id"]]
        first = members[0]
        output.append(
            {
                "id": merged_id,
                "label": merged_id,
                "atype": first.get("atype", ""),
                "path": first.get("path", ""),
                "source_column": first["source_column"],
                "source_label": first["source_label"],
                "cluster": first["cluster"],
                "split": True,
                "rare": False,
                "size": sum(member["size"] for member in members),
                "coverage": round(
                    sum(member["coverage"] for member in members), 4
                ),
                "homogeneity": round(
                    max(member["homogeneity"] for member in members), 4
                ),
                "semantic_label": next(
                    (
                        member.get("semantic_label")
                        for member in members
                        if member.get("semantic_label")
                    ),
                    None,
                ),
                "merged_from": [
                    {
                        "id": member["id"],
                        "source_column": member["source_column"],
                        "cluster": member["cluster"],
                        "rows": list(member["_rows"]),
                    }
                    for member in members
                ],
                "_rows": sorted(
                    {row for member in members for row in member["_rows"]}
                ),
                "_vector_positions": [],
            }
        )
    return output, merged_report


# ── validation ─────────────────────────────────────────────────────────────


def validate_normalization(
    result: dict, config: Optional[NormalizeConfig] = None
) -> dict:
    """Check homogeneity, cluster count, and clean-cluster coverage."""
    config = config or NormalizeConfig()
    issues: list[str] = []
    warnings: list[str] = []

    for source in result.get("report", {}).get("source_columns", []):
        name = source["source_column"]
        if source["concepts"] <= 1:
            continue
        if source["concepts"] > config.max_clusters_per_column:
            warnings.append(
                f"{name}: {source['concepts']} concepts exceeds "
                f"max_clusters_per_column={config.max_clusters_per_column}"
            )
        if source["coverage"] < config.min_coverage:
            warnings.append(
                f"{name}: clean coverage {source['coverage']:.2f} is below "
                f"min_coverage={config.min_coverage:.2f}"
            )
        for cluster in source["clusters"]:
            if (
                not cluster["rare"]
                and cluster["homogeneity"] > config.homogeneity_threshold
            ):
                issues.append(
                    f"{cluster['column']}: homogeneity "
                    f"{cluster['homogeneity']:.3f} exceeds "
                    f"{config.homogeneity_threshold:.3f}"
                )

    return {
        "ok": not issues,
        "issues": issues,
        "warnings": warnings,
    }


def normalize_tables(
    data: dict,
    config: Optional[NormalizeConfig] = None,
    embedder: Optional[CellEmbedder] = None,
) -> dict:
    """Normalize every table in a ``discover_tables`` result."""
    tables = [
        normalize_table(table, config=config, embedder=embedder)
        for table in data.get("tables", [])
    ]
    return {
        **data,
        "tables": tables,
        "normalization": {
            "tables": len(tables),
            "ok": all(table["validation"]["ok"] for table in tables),
        },
    }


__all__ = [
    "DEFAULT_CONTEXT_TEMPLATE",
    "DEFAULT_MODEL_NAME",
    "CellEmbedder",
    "NormalizeConfig",
    "SourceColumn",
    "cluster_vectors",
    "neutral_column_name",
    "normalize_table",
    "normalize_tables",
    "validate_normalization",
]
