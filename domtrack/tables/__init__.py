"""``domtrack tables`` — extract, label, and display HTML tables.

Browser-free discovery over a folded trace
(:mod:`domtrack.tables.discovery`), optional LLM semantic labeling
(:mod:`domtrack.tables.labeling`), and rich / plain / markdown / csv rendering
(:mod:`domtrack.tables.render`). The command-line surface is
``domtrack tables <extract|label|show>``.

The provider SDKs (``openai`` / ``anthropic``) and the display helpers
(``rich`` / ``tabulate``) are imported lazily, so importing this package never
requires them.
"""

from .discovery import (
    Atom,
    Document,
    DiscoveryConfig,
    DiscoveryPolicy,
    Interfaces,
    Node,
    TableDiscoverer,
    build_tree,
    default_path_sim,
    default_sig,
    default_values,
    discover,
    discover_across_documents,
    discover_tables,
    lcs_path_sim,
)
from .labeling import LabelConfig, label_tables
from .render import RenderOptions, render as render_tables

__all__ = [
    "Node", "Atom", "Document", "Interfaces", "DiscoveryConfig",
    "DiscoveryPolicy", "TableDiscoverer",
    "build_tree", "default_sig", "default_values", "default_path_sim",
    "lcs_path_sim", "discover", "discover_across_documents", "discover_tables",
    "LabelConfig", "label_tables",
    "RenderOptions", "render_tables",
]
