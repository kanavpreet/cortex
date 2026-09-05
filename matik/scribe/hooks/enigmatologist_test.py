"""Tests for EnigmatologistCorrelationHook."""

import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from common.models.incidentio_incident import IncidentIOIncident
from scribe.hooks.enigmatologist import EnigmatologistCorrelationHook

_QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/123/enig-queue"

_INCIDENT = IncidentIOIncident(
    incident_id="01K3H5K30V3TECAF9G2HD1X5ZB",
    reference_id="INC-123",
    severity="minor",
    status="active",
    slack_channel_id="C12345678",
    visibility="public",
    created_at=datetime(2025, 1, 15, 12, 0, 0),
    reported_at=datetime(2025, 1, 15, 12, 0, 0),
    updated_at=datetime(2025, 1, 15, 12, 0, 0),
    description_summary="Service outage in us-east-1",
    affected_services=["svc-a", "svc-b"],
    incident_channel_summary="Team is investigating elevated error rates",
)


class TestEnigmatologistCorrelationHook:
    def _make_hook(self) -> tuple[EnigmatologistCorrelationHook, MagicMock]:
        sqs = MagicMock()
        sqs.send_message = MagicMock(return_value={"MessageId": "msg-1"})
        hook = EnigmatologistCorrelationHook(sqs_client=sqs, queue_url=_QUEUE_URL)
        return hook, sqs

    @pytest.mark.asyncio
    async def test_sends_to_correct_queue(self) -> None:
        hook, sqs = self._make_hook()
        await hook.run(_INCIDENT)
        sqs.send_message.assert_called_once()
        call_kwargs = sqs.send_message.call_args[1]
        assert call_kwargs["QueueUrl"] == _QUEUE_URL

    @pytest.mark.asyncio
    async def test_payload_matches_correlation_request_schema(self) -> None:
        """Payload fields match the CorrelationRequest shape from api/routes/incidentio.py."""
        hook, sqs = self._make_hook()
        await hook.run(_INCIDENT)

        body = json.loads(sqs.send_message.call_args[1]["MessageBody"])
        assert body["reference_id"] == "INC-123"
        assert body["description_summary"] == "Service outage in us-east-1"
        assert body["affected_services"] == ["svc-a", "svc-b"]
        assert body["incident_channel_summary"] == (
            "Team is investigating elevated error rates"
        )
        assert "created_at" in body

    @pytest.mark.asyncio
    async def test_none_fields_are_included(self) -> None:
        """Null optional fields are present as None (not omitted) in the payload."""
        incident = IncidentIOIncident(
            incident_id="01K3H5K30V3TECAF9G2HD1X5ZB",
            reference_id="INC-456",
            severity="minor",
            status="active",
            slack_channel_id="C12345678",
            visibility="public",
            created_at=datetime(2025, 1, 15, 12, 0, 0),
            reported_at=datetime(2025, 1, 15, 12, 0, 0),
            updated_at=datetime(2025, 1, 15, 12, 0, 0),
        )
        hook, sqs = self._make_hook()
        await hook.run(incident)

        body = json.loads(sqs.send_message.call_args[1]["MessageBody"])
        assert body["description_summary"] is None
        assert body["affected_services"] is None
        assert body["incident_channel_summary"] is None

    @pytest.mark.asyncio
    async def test_sqs_failure_propagates(self) -> None:
        """SQS errors propagate so HookRunner can swallow them."""
        sqs = MagicMock()
        sqs.send_message = MagicMock(side_effect=Exception("SQS unavailable"))
        hook = EnigmatologistCorrelationHook(sqs_client=sqs, queue_url=_QUEUE_URL)

        with pytest.raises(Exception, match="SQS unavailable"):
            await hook.run(_INCIDENT)
