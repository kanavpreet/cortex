"""Data source registry.

Importing this package self-populates the ``DataSourceSpec`` registry: each
spec module below calls ``register_source`` at import time (the same pattern as
``chronicler/transformers/__init__.py``). Import the spec modules for their
registration side effects only.
"""

from common.datasources import (  # noqa: F401  (registration side effects)
    ghe_pr,
    incident_channel_summary,
    incidentio,
    jira,
)
from common.datasources.registry import (
    DataSourceSpec,
    all_sources,
    get_source,
    register_source,
)

__all__ = [
    "DataSourceSpec",
    "all_sources",
    "get_source",
    "register_source",
]
