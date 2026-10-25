from __future__ import annotations

from dataclasses import dataclass, field


DEFAULT_DYNAMIC_CLASSES: tuple[str, ...] = (
    "d-none", "d-tablecell", "d-block", "active", "addctived",
    "show", "hidden", "fade", "collapse", "collapsing", "open", "closed",
)


@dataclass
class TracerConfig:
    url: str
    target_selector: str | None = None
    viewport: dict[str, int] = field(
        default_factory=lambda: {"width": 1440, "height": 900}
    )
    headless: bool = False
    goto_timeout_ms: int = 360_000
    wait_until: str = "domcontentloaded"

    # Mutation capture limits
    max_text: int = 500
    max_html: int = 2000
    max_nodes: int = 50
    dynamic_classes: tuple[str, ...] = DEFAULT_DYNAMIC_CLASSES

    # Event capture — mousemove intentionally excluded
    capture_events: bool = True
    scroll_throttle_ms: int = 100

    # Recording mode
    duration_ms: int | None = None

    output_dir: str = "traces"

    def __post_init__(self) -> None:
        if not self.url:
            raise ValueError("url is required")
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("duration_ms must be >= 0")