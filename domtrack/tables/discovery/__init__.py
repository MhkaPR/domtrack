"""Table discovery package.

Public API is re-exported here for backwards compatibility. New code should
prefer the class-based extension points:

* :class:`DiscoveryPolicy` — subclass to customize signatures, values,
  path similarity, and visibility.
* :class:`TableDiscoverer` — orchestrator; construct with a
  :class:`DiscoveryConfig`.
"""

from __future__ import annotations

from . import columns, config, engine, features, labels, model, policy
from .columns import ColumnExtractor, _make_column, _path_key, extract_columns
from .config import DiscoveryConfig
from .features import (
    COLUMN_FEATURES,
    PAIRWISE_FEATURES,
    FeatureVectorizer,
    column_features,
    cosine_similarity,
    feature_similarity,
    node_features,
    page_features,
    pairwise_features,
    similarity_matrix,
    value_types,
)
from .engine import (
    TableDiscoverer,
    _discover,
    build_table,
    cluster_children,
    discover,
    discover_across_documents,
    discover_tables,
    group_similarity,
    is_grouping,
    mark_covered,
    shape,
    shape_similarity,
    should_skip_as_row,
)
from .labels import (
    best_class,
    column_id,
    column_label,
    dedupe,
    fingerprint_similarity,
    is_subset_of,
    path_str,
    row_identity,
    table_fingerprint,
)
from .model import (
    SKIP_TAGS,
    VALUE_ATTRS,
    Atom,
    Document,
    Node,
    build_tree,
    clean,
    compute_ever_visible,
    ever_visible_predicate,
    is_hidden_by_attrs,
    skip_subtree,
)
from .policy import (
    DiscoveryPolicy,
    FunctionPolicy,
    Interfaces,
    component_similarity,
    default_is_visible,
    default_path_sim,
    default_sig,
    default_values,
    jaccard,
    lcs_path_sim,
    path_similarity,
)

__all__ = [
    "SKIP_TAGS", "VALUE_ATTRS", "Atom", "Document", "Node", "build_tree",
    "clean", "compute_ever_visible", "ever_visible_predicate",
    "is_hidden_by_attrs", "skip_subtree",
    "DiscoveryPolicy", "FunctionPolicy", "Interfaces", "component_similarity",
    "default_is_visible", "default_path_sim", "default_sig", "default_values",
    "jaccard", "lcs_path_sim", "path_similarity",
    "DiscoveryConfig", "ColumnExtractor", "extract_columns",
    "TableDiscoverer", "discover", "discover_tables",
    "discover_across_documents", "shape", "shape_similarity",
    "group_similarity", "is_grouping", "should_skip_as_row",
    "cluster_children", "build_table", "mark_covered",
    "best_class", "path_str", "column_id", "column_label", "row_identity",
    "table_fingerprint", "fingerprint_similarity", "is_subset_of", "dedupe",
    "FeatureVectorizer", "column_features", "cosine_similarity",
    "feature_similarity", "node_features", "page_features",
    "pairwise_features", "similarity_matrix", "value_types",
    "COLUMN_FEATURES", "PAIRWISE_FEATURES",
    "columns", "config", "engine", "features", "labels", "model", "policy",
]
