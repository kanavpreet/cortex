"""Unit tests for braintrust_client.py.

Mocks the underlying genai_studio BTQL client — these never make a real
Braintrust call. The mocked response shapes mirror real production traces:
the root ``incident_correlation`` span carries ``created``/``root_span_id``
only (``output: null``); the actual LLM selection is on a child span's
``output[0]["content"]``.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from common.clients.braintrust_client import BraintrustClient

_ENTITY_TYPE_BY_FIELD = {"biztech_github": "github_pr", "jira": "jira_tcmr"}


@pytest.fixture(autouse=True)
def _interactive_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test defaults to an interactive context, so the real (unmocked)
    ServiceIatCredential/init_httpx_client path is never exercised except by
    the dedicated tests below that mock those two dependencies explicitly."""
    mock_ctx = MagicMock()
    mock_ctx.is_interactive = True
    monkeypatch.setattr(
        "common.clients.braintrust_client.current_context", lambda: mock_ctx
    )


def _root_span(created: str, root_span_id: str) -> dict[str, object]:
    return {"created": created, "root_span_id": root_span_id}


def _child_span_with_output(content: str) -> dict[str, object]:
    return {
        "span_attributes": {"name": "bedrock.chat"},
        "output": [{"content": content, "role": "assistant"}],
    }


def _root_span_no_output() -> dict[str, object]:
    return {"span_attributes": {"name": "incident_correlation"}, "output": None}


@patch("common.clients.braintrust_client._GenaiBTQLClient")
def test_returns_empty_list_when_no_trace_in_window(mock_btql_class: MagicMock) -> None:
    mock_btql = MagicMock()
    mock_btql.btql_paginated.return_value = [
        _root_span(
            "2026-08-01T14:30:00.000Z", "root-1"
        ),  # 54 min later, outside window
    ]
    mock_btql_class.return_value = mock_btql

    client = BraintrustClient(
        braintrust_project_id="proj-1",
        entity_type_by_field=_ENTITY_TYPE_BY_FIELD,
        trace_environment="production",
    )
    result = client.get_investigation_time_correlations(
        incident_id="INC-1234",
        entity_created_at=datetime(2026, 8, 1, 13, 35, 57),
        window_minutes=20,
    )

    assert result == []


@patch("common.clients.braintrust_client._GenaiBTQLClient")
def test_excludes_trace_created_before_entity(mock_btql_class: MagicMock) -> None:
    """A span created before entity_created_at must not count as "within the
    window" just because its magnitude is within window_minutes -- a stale
    or otherwise-unrelated trace from before the incident existed shouldn't
    be picked as its investigation-time correlation."""
    mock_btql = MagicMock()
    mock_btql.btql_paginated.return_value = [
        _root_span("2026-08-01T13:30:00.000Z", "root-before"),  # ~6 min earlier
    ]
    mock_btql_class.return_value = mock_btql

    client = BraintrustClient(
        braintrust_project_id="proj-1",
        entity_type_by_field=_ENTITY_TYPE_BY_FIELD,
        trace_environment="production",
    )
    result = client.get_investigation_time_correlations(
        incident_id="INC-1234",
        entity_created_at=datetime(2026, 8, 1, 13, 35, 57),
        window_minutes=20,
    )

    assert result == []


@patch("common.clients.braintrust_client._GenaiBTQLClient")
def test_unions_correlations_across_all_traces_within_window(
    mock_btql_class: MagicMock,
) -> None:
    """Multiple reruns can fall inside the investigation window (the engine
    reruns on every incident update) -- every one of them must be queried
    and their selections unioned, not just the earliest one, or a later
    rerun's newly-surfaced entity would be silently dropped."""
    mock_btql = MagicMock()
    mock_btql.btql_paginated.side_effect = [
        # First call: root spans (unordered)
        [
            _root_span("2026-08-01T13:36:17.000Z", "root-later"),
            _root_span("2026-08-01T13:36:00.000Z", "root-earliest"),
        ],
        # Second call: spans for the earliest root_span_id
        [
            _root_span_no_output(),
            _child_span_with_output(
                '{"biztech_github": [{"id": "111", "score": 0.5, "reasoning": "r1"}],'
                ' "jira": []}'
            ),
        ],
        # Third call: spans for the later root_span_id
        [
            _root_span_no_output(),
            _child_span_with_output(
                '{"biztech_github": [],'
                ' "jira": [{"id": "TCMR-1", "score": 0.5, "reasoning": "r2"}]}'
            ),
        ],
    ]
    mock_btql_class.return_value = mock_btql

    client = BraintrustClient(
        braintrust_project_id="proj-1",
        entity_type_by_field=_ENTITY_TYPE_BY_FIELD,
        trace_environment="production",
    )
    result = client.get_investigation_time_correlations(
        incident_id="INC-1234",
        entity_created_at=datetime(2026, 8, 1, 13, 35, 57),
        window_minutes=20,
    )

    second_call_query = mock_btql.btql_paginated.call_args_list[1].args[0]
    third_call_query = mock_btql.btql_paginated.call_args_list[2].args[0]
    assert "root-earliest" in second_call_query
    assert "root-later" in third_call_query
    entity_ids = {c.entity_id for c in result}
    assert entity_ids == {"111", "TCMR-1"}


@patch("common.clients.braintrust_client._GenaiBTQLClient")
def test_earliest_rerun_wins_over_later_for_same_entity(
    mock_btql_class: MagicMock,
) -> None:
    """When the same entity is selected by more than one in-window rerun,
    the earliest rerun's row wins -- the same PR/TCMR tends to get reselected
    rerun after rerun with an essentially unchanged score/reasoning, and this
    matches ``row_created_at`` on the DB side (set once, at first discovery,
    never overwritten by a later rerun that reselects the same entity)."""
    mock_btql = MagicMock()
    mock_btql.btql_paginated.side_effect = [
        [
            _root_span("2026-08-01T13:36:00.000Z", "root-earliest"),
            _root_span("2026-08-01T13:36:17.000Z", "root-later"),
        ],
        [
            _child_span_with_output(
                '{"biztech_github": [{"id": "111", "score": 0.5, "reasoning": "first seen"}],'
                ' "jira": []}'
            ),
        ],
        [
            _child_span_with_output(
                '{"biztech_github": [{"id": "111", "score": 0.9, "reasoning": "reselected"}],'
                ' "jira": []}'
            ),
        ],
    ]
    mock_btql_class.return_value = mock_btql

    client = BraintrustClient(
        braintrust_project_id="proj-1",
        entity_type_by_field=_ENTITY_TYPE_BY_FIELD,
        trace_environment="production",
    )
    result = client.get_investigation_time_correlations(
        incident_id="INC-1234",
        entity_created_at=datetime(2026, 8, 1, 13, 35, 57),
        window_minutes=20,
    )

    assert len(result) == 1
    assert result[0].reasoning == "first seen"
    assert result[0].base_score == 0.5


@patch("common.clients.braintrust_client._GenaiBTQLClient")
def test_parses_github_pr_and_jira_tcmr_matches(mock_btql_class: MagicMock) -> None:
    mock_btql = MagicMock()
    mock_btql.btql_paginated.side_effect = [
        [_root_span("2026-08-01T13:36:00.000Z", "root-1")],
        [
            _root_span_no_output(),
            _child_span_with_output(
                '{"biztech_github": [{"id": "122036", "score": 0.4, "reasoning": "same service"}],'
                ' "jira": [{"id": "TCMR-23472", "score": 0.4, "reasoning": "recent change"}]}'
            ),
        ],
    ]
    mock_btql_class.return_value = mock_btql

    client = BraintrustClient(
        braintrust_project_id="proj-1",
        entity_type_by_field=_ENTITY_TYPE_BY_FIELD,
        trace_environment="production",
    )
    result = client.get_investigation_time_correlations(
        incident_id="INC-1234",
        entity_created_at=datetime(2026, 8, 1, 13, 35, 57),
        window_minutes=20,
    )

    assert len(result) == 2
    by_type = {c.entity_type: c for c in result}
    assert by_type["github_pr"].entity_id == "122036"
    assert by_type["github_pr"].anchor_entity_id == "INC-1234"
    assert by_type["github_pr"].correlation_type == "LLM"
    assert by_type["jira_tcmr"].entity_id == "TCMR-23472"


@patch("common.clients.braintrust_client._GenaiBTQLClient")
def test_drops_candidates_below_min_llm_score_by_default(
    mock_btql_class: MagicMock,
) -> None:
    """The raw trace includes every candidate the LLM evaluated, including
    ones it scored below threshold and explicitly rejected (e.g. "no incident
    to correlate with"). Production's assign_correlations_by_llm node drops
    these before persisting -- if this client didn't re-apply the same
    min_llm_score default, a rejected candidate would show up here as if
    Matik had actually selected it, with no matching row in the DB."""
    mock_btql = MagicMock()
    mock_btql.btql_paginated.side_effect = [
        [_root_span("2026-08-01T13:36:00.000Z", "root-1")],
        [
            _root_span_no_output(),
            _child_span_with_output(
                '{"biztech_github": [],'
                ' "jira": [{"id": "TCMR-1", "score": 0.05,'
                ' "reasoning": "has no incident to correlate with"}]}'
            ),
        ],
    ]
    mock_btql_class.return_value = mock_btql

    client = BraintrustClient(
        braintrust_project_id="proj-1",
        entity_type_by_field=_ENTITY_TYPE_BY_FIELD,
        trace_environment="production",
    )
    result = client.get_investigation_time_correlations(
        incident_id="INC-1234",
        entity_created_at=datetime(2026, 8, 1, 13, 35, 57),
        window_minutes=20,
    )

    assert result == []


@patch("common.clients.braintrust_client._GenaiBTQLClient")
def test_returns_empty_list_when_no_output_span_found(
    mock_btql_class: MagicMock,
) -> None:
    mock_btql = MagicMock()
    mock_btql.btql_paginated.side_effect = [
        [_root_span("2026-08-01T13:36:00.000Z", "root-1")],
        [_root_span_no_output()],  # no child span with output at all
    ]
    mock_btql_class.return_value = mock_btql

    client = BraintrustClient(
        braintrust_project_id="proj-1",
        entity_type_by_field=_ENTITY_TYPE_BY_FIELD,
        trace_environment="production",
    )
    result = client.get_investigation_time_correlations(
        incident_id="INC-1234",
        entity_created_at=datetime(2026, 8, 1, 13, 35, 57),
        window_minutes=20,
    )

    assert result == []


@patch("common.clients.braintrust_client._GenaiBTQLClient")
def test_returns_empty_list_on_malformed_output(mock_btql_class: MagicMock) -> None:
    mock_btql = MagicMock()
    mock_btql.btql_paginated.side_effect = [
        [_root_span("2026-08-01T13:36:00.000Z", "root-1")],
        [_child_span_with_output("not valid json")],
    ]
    mock_btql_class.return_value = mock_btql

    client = BraintrustClient(
        braintrust_project_id="proj-1",
        entity_type_by_field=_ENTITY_TYPE_BY_FIELD,
        trace_environment="production",
    )
    result = client.get_investigation_time_correlations(
        incident_id="INC-1234",
        entity_created_at=datetime(2026, 8, 1, 13, 35, 57),
        window_minutes=20,
    )

    assert result == []


@patch("common.clients.braintrust_client._GenaiBTQLClient")
def test_query_filters_by_incident_id_and_operation(mock_btql_class: MagicMock) -> None:
    mock_btql = MagicMock()
    mock_btql.btql_paginated.return_value = []
    mock_btql_class.return_value = mock_btql

    client = BraintrustClient(
        braintrust_project_id="proj-1",
        entity_type_by_field=_ENTITY_TYPE_BY_FIELD,
        trace_environment="production",
    )
    client.get_investigation_time_correlations(
        incident_id="INC-1234",
        entity_created_at=datetime(2026, 8, 1, 13, 35, 57),
        window_minutes=20,
    )

    query = mock_btql.btql_paginated.call_args_list[0].args[0]
    assert "metadata.incident_id = 'INC-1234'" in query
    assert "metadata.operation = 'incident_correlation'" in query


@patch("common.clients.braintrust_client._GenaiBTQLClient")
def test_query_filters_by_caller_supplied_trace_environment(
    mock_btql_class: MagicMock,
) -> None:
    """The shared Braintrust project means a sandbox/staging rerun against a
    real incident id must never be mistaken for another environment's run --
    and the environment filtered on is whatever the caller passes in, not a
    value hardcoded on the client."""
    mock_btql = MagicMock()
    mock_btql.btql_paginated.return_value = []
    mock_btql_class.return_value = mock_btql

    client = BraintrustClient(
        braintrust_project_id="proj-1",
        entity_type_by_field=_ENTITY_TYPE_BY_FIELD,
        trace_environment="staging",
    )
    client.get_investigation_time_correlations(
        incident_id="INC-1234",
        entity_created_at=datetime(2026, 8, 1, 13, 35, 57),
        window_minutes=20,
    )

    query = mock_btql.btql_paginated.call_args_list[0].args[0]
    assert """metadata."deployment.environment" = 'staging'""" in query


@patch("common.clients.braintrust_client._GenaiBTQLClient")
def test_escapes_single_quotes_in_incident_id(mock_btql_class: MagicMock) -> None:
    mock_btql = MagicMock()
    mock_btql.btql_paginated.return_value = []
    mock_btql_class.return_value = mock_btql

    client = BraintrustClient(
        braintrust_project_id="proj-1",
        entity_type_by_field=_ENTITY_TYPE_BY_FIELD,
        trace_environment="production",
    )
    client.get_investigation_time_correlations(
        incident_id="INC-'; drop",
        entity_created_at=datetime(2026, 8, 1, 13, 35, 57),
        window_minutes=20,
    )

    query = mock_btql.btql_paginated.call_args_list[0].args[0]
    assert "INC-''; drop" in query


def test_uses_default_credential_in_interactive_context() -> None:
    """The autouse fixture keeps this test in an interactive context --
    confirm that skips the ServiceIatCredential branch entirely (no
    attribute clobbering, no real network calls)."""
    with patch("common.clients.braintrust_client._GenaiBTQLClient") as mock_btql_class:
        mock_btql = MagicMock()
        mock_btql_class.return_value = mock_btql
        original_http = mock_btql.http

        BraintrustClient(
            braintrust_project_id="proj-1",
            entity_type_by_field=_ENTITY_TYPE_BY_FIELD,
            trace_environment="production",
        )

        assert mock_btql.http is original_http


@patch("common.clients.braintrust_client.init_httpx_client")
@patch("common.clients.braintrust_client.ServiceIatCredential")
@patch("common.clients.braintrust_client._GenaiBTQLClient")
def test_uses_service_iat_credential_outside_interactive_context(
    mock_btql_class: MagicMock,
    mock_credential_class: MagicMock,
    mock_init_httpx: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside an interactive session (i.e. running as a k8s pod), the
    default credential sends no auth header at all -- ServiceIatCredential
    fixes that. See module docstring."""
    mock_ctx = MagicMock()
    mock_ctx.is_interactive = False
    monkeypatch.setattr(
        "common.clients.braintrust_client.current_context", lambda: mock_ctx
    )
    mock_btql = MagicMock()
    mock_btql_class.return_value = mock_btql
    mock_credential = MagicMock()
    mock_credential_class.return_value = mock_credential
    mock_authed_http = MagicMock()
    mock_init_httpx.return_value = mock_authed_http

    BraintrustClient(
        braintrust_project_id="proj-1",
        entity_type_by_field=_ENTITY_TYPE_BY_FIELD,
        trace_environment="production",
    )

    mock_init_httpx.assert_called_once_with(
        custom_credential=mock_credential, timeout=30
    )
    assert mock_btql.http is mock_authed_http
