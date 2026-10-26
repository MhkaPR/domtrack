from __future__ import annotations

from typing import Iterable, Optional


class ThirdPartyDetector:
    """Heuristically detect third-party widgets from selector or element id."""

    DEFAULT_HINTS: tuple[str, ...] = (
        "flowchat", "goftino", "raychat", "crisp", "intercom",
        "tawk", "livechat", "olark", "drift", "zendesk",
        "freshchat", "chat-widget", "widget-frame", "support-widget",
    )

    def __init__(self, hints: Optional[Iterable[str]] = None):
        self.hints = tuple(h.lower() for h in (hints or self.DEFAULT_HINTS))

    def is_third_party(self, selector: str = "", element_id: str = "") -> bool:
        text = f"{selector} {element_id}".lower()
        return any(h in text for h in self.hints)