from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Target:
    tag: Optional[str] = None
    id: Optional[str] = None
    selector: Optional[str] = None

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "Target":
        d = d or {}
        return cls(
            tag=d.get("tag"),
            id=d.get("id"),
            selector=d.get("selector"),
        )


@dataclass
class NodeInfo:
    id: Optional[str] = None
    selector: Optional[str] = None
    html: Optional[str] = None

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> Optional["NodeInfo"]:
        if not d:
            return None
        return cls(
            id=d.get("id"),
            selector=d.get("selector"),
            html=d.get("html"),
        )


@dataclass
class Mutation:
    t: int
    type: str
    target: Target
    attribute_name: Optional[str] = None
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    added: list[NodeInfo] = field(default_factory=list)
    removed: list[NodeInfo] = field(default_factory=list)
    added_count: int = 0        # ← new
    removed_count: int = 0      # ← new

    @classmethod
    def from_dict(cls, d: dict) -> "Mutation":
        added = [n for n in (NodeInfo.from_dict(x) for x in d.get("added") or []) if n]
        removed = [n for n in (NodeInfo.from_dict(x) for x in d.get("removed") or []) if n]

        ac_raw = d.get("addedCount")
        rc_raw = d.get("removedCount")
        return cls(
            t=int(d.get("t", 0)),
            type=d.get("type", ""),
            target=Target.from_dict(d.get("target")),
            attribute_name=d.get("attributeName"),
            old_value=d.get("oldValue"),
            new_value=d.get("newValue"),
            added=added,
            removed=removed,
            added_count=int(ac_raw) if ac_raw is not None else len(added),
            removed_count=int(rc_raw) if rc_raw is not None else len(removed),
        )

@dataclass
class TraceMeta:
    url: str
    viewport: dict[str, int]
    title: Optional[str] = None
    captured_at: Optional[str] = None

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "TraceMeta":
        d = d or {}
        vp = d.get("viewport") or {}
        width = int(vp.get("width") or vp.get("w") or 1440)
        height = int(vp.get("height") or vp.get("h") or 900)
        return cls(
            url=d.get("url", ""),
            viewport={"width": width, "height": height},
            title=d.get("title"),
            captured_at=d.get("capturedAt"),
        )


@dataclass
class Trace:
    meta: TraceMeta
    mutations: list[Mutation]
    events: list[Event] = field(default_factory=list)

    @classmethod
    def load(cls, path: str) -> "Trace":
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        return cls(
            meta=TraceMeta.from_dict(raw.get("meta")),
            mutations=[Mutation.from_dict(m) for m in raw.get("mutations", [])],
        )

    def group_by_time(self) -> dict[int, list[Mutation]]:
        by_t: dict[int, list[Mutation]] = defaultdict(list)
        for m in self.mutations:
            by_t[m.t].append(m)
        return dict(by_t)

@dataclass
class EventTarget:
    tag: Optional[str] = None
    id: Optional[str] = None
    selector: Optional[str] = None
    text: Optional[str] = None

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> Optional["EventTarget"]:
        if not d:
            return None
        return cls(
            tag=d.get("tag"),
            id=d.get("id"),
            selector=d.get("selector"),
            text=d.get("text"),
        )


@dataclass
class Event:
    t: int
    kind: str  # click / mousedown / mouseup / keydown / keyup / input /
               # change / scroll / wheel / mousemove / pointermove /
               # focus / blur / submit / dblclick / contextmenu
    target: Optional[EventTarget] = None

    # Pointer
    client_x: Optional[int] = None
    client_y: Optional[int] = None
    button: Optional[int] = None
    buttons: Optional[int] = None

    # Keyboard
    key: Optional[str] = None
    code: Optional[str] = None
    repeat: bool = False

    # Input
    value: Optional[str] = None

    # Scroll
    scroll_x: Optional[int] = None
    scroll_y: Optional[int] = None
    is_document_scroll: bool = False

    # Wheel
    delta_x: Optional[float] = None
    delta_y: Optional[float] = None

    # Modifiers
    ctrl: bool = False
    shift: bool = False
    alt: bool = False
    meta: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        m = d.get("modifiers") or {}
        return cls(
            t=int(d.get("t", 0)),
            kind=d.get("kind", ""),
            target=EventTarget.from_dict(d.get("target")),
            client_x=d.get("clientX"),
            client_y=d.get("clientY"),
            button=d.get("button"),
            buttons=d.get("buttons"),
            key=d.get("key"),
            code=d.get("code"),
            repeat=bool(d.get("repeat", False)),
            value=d.get("value"),
            scroll_x=d.get("scrollX"),
            scroll_y=d.get("scrollY"),
            is_document_scroll=bool(d.get("isDocument", False)),
            delta_x=d.get("deltaX"),
            delta_y=d.get("deltaY"),
            ctrl=bool(m.get("ctrl", False)),
            shift=bool(m.get("shift", False)),
            alt=bool(m.get("alt", False)),
            meta=bool(m.get("meta", False)),
        )