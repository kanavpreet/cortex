"""Root-cause source registry.

Importing this package self-populates the ``RootCauseSourceSpec`` registry:
each spec module below calls ``register_root_cause_source`` at import time
(the same pattern as ``common/datasources/__init__.py``). Import the spec
modules for their registration side effects only.
"""

from audit.root_cause_coverage.sources import (  # noqa: F401  (registration side effects)
    github_pr,
    jira_tcmr,
)
from audit.root_cause_coverage.sources.registry import (
    RootCauseSourceSpec,
    all_root_cause_sources,
    get_root_cause_source,
    register_root_cause_source,
)

__all__ = [
    "RootCauseSourceSpec",
    "all_root_cause_sources",
    "get_root_cause_source",
    "register_root_cause_source",
]
