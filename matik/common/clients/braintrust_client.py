"""Braintrust client for reading correlation traces back out.

Wraps GenAI Studio's already-authenticated ``BraintrustClient`` (BTQL, via
``genai_studio.braintrust.btql.client``) rather than hand-rolling REST calls —
it already handles auth, retries, and pagination, and is the tool GenAI
Studio's own trace-inspection code uses internally.

- A correlation rerun is tagged on its root ``incident_correlation`` span with
  ``metadata.incident_id`` and ``metadata.entity_created_at``. Traces from
  before this tagging existed lack both fields and are invisible here.
- The LLM's actual selection lives on a CHILD span (name ``bedrock.chat`` or
  similar, per-provider), in ``output[0]["content"]`` — the same JSON string
  shape ``parse_llm_correlations`` already parses in production. The root
  span itself always has ``output: null``. That raw completion includes every
  candidate the LLM evaluated, including ones it scored below threshold and
  explicitly rejected -- ``min_llm_score``/``speculative_patterns``/
  ``speculative_max_score`` re-apply production's own filter so this only
  returns what production actually selected (and would have persisted).
- The investigation-time window is computed in Python, not BTQL: BTQL's
  ``to_datetime`` doesn't parse ``entity_created_at``'s naive ISO string the
  same way it parses ``created``'s Z-suffixed one, so a BTQL-side date
  comparison silently evaluates to null. Root spans are fetched with a plain
  equality filter instead, and the window is applied after.
- ``_GenaiBTQLClient()`` with no args authenticates via ``DefaultIatCredential``,
  which has no branch for a plain Kubernetes/AirMesh pod (only BigAir,
  Sandcastle, and interactive sessions) — it silently sends no auth header at
  all, and BTQL (a permission-checked endpoint, unlike trace export) rejects
  that. ``ServiceIatCredential`` mints an IAT for the pod's own service
  identity instead, which braintrust-proxy resolves to ``svc-matik`` (granted
  Braintrust project read access).
- The Braintrust project is shared across sandbox/staging/production (all
  three are BTQL-allowlisted), so the root-span query filters on
  ``metadata."deployment.environment"``, scoped to whichever environment the
  caller passes as ``trace_environment`` — otherwise a sandbox/staging
  correlation rerun against a real incident id would be indistinguishable
  from a genuine run in the environment the caller actually cares about.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from airbnb_identity import ServiceIatCredential, current_context
from genai_studio.braintrust.btql.client import BraintrustClient as _GenaiBTQLClient
from genai_studio.braintrust.proxy.api_client import init_httpx_client

from common.models.reliability_correlation import ReliabilityCorrelation
from common.utils import log_utils
from common.utils.datetime_utils import parse_timestamp_to_utc
from enigmatologist.correlations.reliability.incident.nodes import (
    parse_llm_correlations,
)

logger = log_utils.get_logger(__name__)


def _escape_btql_literal(value: str) -> str:
    """Escape a string for interpolation into a BTQL string literal."""
    return value.replace("'", "''")


@dataclass
class BraintrustClient:
    """Reads correlation traces for a single Braintrust project, scoped to
    one deployment environment.

    Example usage:
        client = BraintrustClient(
            braintrust_project_id=config.llm_tracing.braintrust_project_id,
            entity_type_by_field={"biztech_github": "github_pr", "jira": "jira_tcmr"},
            trace_environment="production",
        )
        correlations = client.get_investigation_time_correlations(
            incident_id="INC-8077",
            entity_created_at=incident.created_at,
            window_minutes=20,
        )
    """

    braintrust_project_id: str
    entity_type_by_field: dict[str, str]
    """Maps a field name on enigmatologist's correlation-result model (e.g.
    "biztech_github") to this caller's own entity-type vocabulary (e.g.
    "github_pr"). This client has no opinion on what those source types
    are -- the caller (the root-cause-coverage audit) owns that mapping."""

    trace_environment: str
    """The environment of traces to read (e.g. "production", "sandbox")."""

    min_llm_score: float = 0.3
    """Must mirror EnigmatologistConfig.min_llm_score (see
    matik-enigmatologist-config.yml) -- production's assign_correlations_by_llm
    node drops any candidate below this score before it's ever persisted, so
    reading the raw trace without the same threshold would surface candidates
    the LLM considered and rejected (e.g. "no incident to correlate with") as
    if Matik had actually selected them."""

    speculative_patterns: list[str] = field(default_factory=list)
    """Must mirror EnigmatologistConfig.speculative_patterns -- see
    min_llm_score."""

    speculative_max_score: float = 0.7
    """Must mirror EnigmatologistConfig.speculative_max_score -- see
    min_llm_score."""

    _btql: _GenaiBTQLClient = field(init=False)

    def __post_init__(self) -> None:
        self._btql = _GenaiBTQLClient()
        if not current_context().is_interactive:
            # See module docstring -- the default credential sends no auth
            # header at all from a plain Kubernetes pod, so BTQL rejects it.
            self._btql.http = init_httpx_client(
                custom_credential=ServiceIatCredential(), timeout=30
            )

    def get_investigation_time_correlations(
        self,
        incident_id: str,
        entity_created_at: datetime,
        window_minutes: int,
    ) -> list[ReliabilityCorrelation]:
        """Return the union of Matik's correlation selections from every
        rerun within the window.

        Correlation reruns on every incident update, so multiple traces can
        exist for one incident within the investigation window -- this unions
        every one of them, the same way the postmortem-time bucket (the DB)
        is itself a union across every rerun ever done for the incident
        (``main.py::_build_persistence_payload`` never drops an entity a
        prior rerun found, it only adds to or refreshes the set). Picking
        just one rerun here would silently drop entities a later in-window
        rerun surfaced that the first one didn't yet know about. The same
        PR/TCMR tends to keep getting selected rerun after rerun with an
        essentially unchanged score/reasoning, so when the same
        ``(entity_type, entity_id)`` is selected by more than one in-window
        rerun, the earliest rerun's version wins -- matching ``row_created_at``
        semantics on the DB side (set once, at first discovery, never
        overwritten by later reruns that reselect the same entity).
        Returns an empty list if no rerun falls in the window, or if none of
        the matching traces have readable output.
        """
        root_spans = self._fetch_root_spans(incident_id)
        in_window = [
            span
            for span in root_spans
            if self._within_window(span, entity_created_at, window_minutes)
        ]
        if not in_window:
            logger.info(
                "no investigation-time correlation trace within window",
                incident_id=incident_id,
                window_minutes=window_minutes,
                candidates=len(root_spans),
            )
            return []

        in_window.sort(key=lambda span: str(span["created"]))
        merged: dict[tuple[str, str], ReliabilityCorrelation] = {}
        for span in in_window:
            for corr in self._extract_correlations(incident_id, span["root_span_id"]):
                merged.setdefault((corr.entity_type, corr.entity_id), corr)
        return list(merged.values())

    def _fetch_root_spans(self, incident_id: str) -> list[dict[str, Any]]:
        # deployment.environment is a resource attribute (see
        # common/llm_tracing/client.py), merged onto every span's own
        # attributes by genai_studio's BraintrustProcessor before export, so
        # it's queryable as metadata just like operation/incident_id. This
        # project is shared across sandbox/staging/production (all three are
        # BTQL-allowlisted -- see braintrust-btql-from-k8s.md), so without
        # this filter a sandbox or staging correlation rerun against a real
        # incident id would be indistinguishable from a genuine run in
        # ``self.trace_environment``.
        query = f"""
select: created, root_span_id
from: project('{_escape_btql_literal(self.braintrust_project_id)}')
filter: metadata.incident_id = '{_escape_btql_literal(incident_id)}' and metadata.operation = 'incident_correlation' and metadata."deployment.environment" = '{_escape_btql_literal(self.trace_environment)}'
"""
        rows: list[dict[str, Any]] = self._btql.btql_paginated(query)
        return rows

    def _within_window(
        self,
        span: dict[str, Any],
        entity_created_at: datetime,
        window_minutes: int,
    ) -> bool:
        created = parse_timestamp_to_utc(span["created"])
        if created is None:
            return False
        delta = created - entity_created_at
        return timedelta(0) <= delta <= timedelta(minutes=window_minutes)

    def _extract_correlations(
        self, incident_id: str, root_span_id: object
    ) -> list[ReliabilityCorrelation]:
        query = f"""
select: *
from: project('{_escape_btql_literal(self.braintrust_project_id)}')
filter: root_span_id = '{_escape_btql_literal(str(root_span_id))}'
"""
        spans: list[dict[str, Any]] = self._btql.btql_paginated(query)
        content = self._find_llm_output(spans)
        if content is None:
            logger.info(
                "investigation-time trace has no LLM output span",
                incident_id=incident_id,
                root_span_id=root_span_id,
            )
            return []

        try:
            result = parse_llm_correlations(
                content,
                min_score=self.min_llm_score,
                speculative_patterns=self.speculative_patterns,
                speculative_max_score=self.speculative_max_score,
            )
        except Exception:
            logger.exception(
                "failed to parse investigation-time correlation output",
                incident_id=incident_id,
                root_span_id=root_span_id,
            )
            return []

        correlations: list[ReliabilityCorrelation] = []
        for field_name, entity_type in self.entity_type_by_field.items():
            for match in getattr(result, field_name):
                correlations.append(
                    ReliabilityCorrelation(
                        anchor_entity_id=incident_id,
                        correlation_type="LLM",
                        entity_type=entity_type,
                        entity_id=match.id,
                        reasoning=match.reasoning,
                        base_score=match.base_score,
                        final_score=match.final_score,
                        scoring_version=match.scoring_version,
                    )
                )
        return correlations

    @staticmethod
    def _find_llm_output(spans: list[dict[str, Any]]) -> str | None:
        """Find the child span carrying the LLM's raw response content.

        The root ``incident_correlation`` span always has ``output: null`` —
        the actual response is on a nested provider span (``bedrock.chat`` or
        similar).
        """
        for span in spans:
            output = span.get("output")
            if not output:
                continue
            first = output[0] if isinstance(output, list) else output
            content = first.get("content") if isinstance(first, dict) else None
            if isinstance(content, str):
                return content
        return None
