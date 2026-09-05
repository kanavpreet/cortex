"""Root cause coverage audit — the first audit type under ``audit/``.

Evaluates whether Matik's incident correlations surface the actual root cause,
continuously, against real production incidents. See the design doc (Slate:
"Automated Root Cause Coverage Audit") for the full spec.
"""

from __future__ import annotations

from audit.root_cause_coverage.pipeline import (
    find_eligible_entities,
    process_entity,
    rescore_entity,
)
from audit.sheet_sync import AuditSpec

SPEC = AuditSpec(
    row_key="reference_id",
    find_eligible_entities=find_eligible_entities,
    process_entity=process_entity,
    rescore_entity=rescore_entity,
)
