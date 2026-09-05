"""Unit tests for EnrichmentRequest model."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from common.models.enricher_messages import EnrichmentRequest


class TestEnrichmentRequestConstruction:
    """Tests for valid and invalid EnrichmentRequest construction."""

    def _valid_data(self) -> dict[str, Any]:
        """Return minimal valid data for an EnrichmentRequest."""
        return {
            "source_type": "incidentio",
            "producer": "historian",
            "task_id": "task-abc-123",
            "entity_id": {"incident_id": "INC-456"},
            "content": {"summary": "Database connection pool exhausted"},
        }

    def test_valid_construction_historian_producer(self) -> None:
        """Valid request with historian producer parses successfully."""
        req = EnrichmentRequest(**self._valid_data())
        assert req.source_type == "incidentio"
        assert req.producer == "historian"
        assert req.task_id == "task-abc-123"
        assert req.entity_id == {"incident_id": "INC-456"}
        assert req.content == {"summary": "Database connection pool exhausted"}

    def test_valid_construction_chronicler_producer(self) -> None:
        """Valid request with chronicler producer parses successfully."""
        data = self._valid_data()
        data["producer"] = "chronicler"
        req = EnrichmentRequest(**data)
        assert req.producer == "chronicler"

    def test_invalid_producer_raises_validation_error(self) -> None:
        """Invalid producer value fails validation."""
        data = self._valid_data()
        data["producer"] = "unknown_service"
        with pytest.raises(ValidationError):
            EnrichmentRequest(**data)

    def test_missing_source_type_raises_validation_error(self) -> None:
        """Missing required source_type field raises ValidationError."""
        data = self._valid_data()
        del data["source_type"]
        with pytest.raises(ValidationError):
            EnrichmentRequest(**data)

    def test_missing_task_id_raises_validation_error(self) -> None:
        """Missing required task_id field raises ValidationError."""
        data = self._valid_data()
        del data["task_id"]
        with pytest.raises(ValidationError):
            EnrichmentRequest(**data)

    def test_empty_content_dict_is_valid(self) -> None:
        """Empty content dict is a valid request."""
        data = self._valid_data()
        data["content"] = {}
        req = EnrichmentRequest(**data)
        assert req.content == {}

    def test_multi_key_entity_id(self) -> None:
        """entity_id can contain multiple keys."""
        data = self._valid_data()
        data["entity_id"] = {"incident_id": "INC-1", "org_id": "airbnb"}
        req = EnrichmentRequest(**data)
        assert req.entity_id["org_id"] == "airbnb"

    def test_multi_key_content(self) -> None:
        """content can contain multiple enrichment fields."""
        data = self._valid_data()
        data["content"] = {
            "summary": "Incident summary text",
            "resolution_statement": "Fixed by restarting the service",
        }
        req = EnrichmentRequest(**data)
        assert "resolution_statement" in req.content


class TestEnrichmentRequestJSONRoundTrip:
    """Tests for JSON serialization and deserialization."""

    def test_model_dump_and_model_validate_round_trip(self) -> None:
        """Serializing to dict and back produces an equivalent model."""
        original = EnrichmentRequest(
            source_type="ghe_pr",
            producer="chronicler",
            task_id="t-001",
            entity_id={"pr_number": "42"},
            content={"original_description": "Adds feature X"},
        )
        dumped = original.model_dump()
        restored = EnrichmentRequest.model_validate(dumped)
        assert restored.source_type == original.source_type
        assert restored.producer == original.producer
        assert restored.task_id == original.task_id
        assert restored.entity_id == original.entity_id
        assert restored.content == original.content

    def test_json_round_trip(self) -> None:
        """JSON encoding and decoding via model_dump_json preserves all fields."""
        original = EnrichmentRequest(
            source_type="jira",
            producer="historian",
            task_id="t-999",
            entity_id={"issue_key": "INFRA-123"},
            content={"issue_description": "Service is down"},
        )
        json_str = original.model_dump_json()
        data = json.loads(json_str)
        restored = EnrichmentRequest.model_validate(data)
        assert restored == original

    def test_model_validate_from_raw_dict(self) -> None:
        """model_validate works with a plain dict input."""
        data = {
            "source_type": "incidentio",
            "producer": "historian",
            "task_id": "abc",
            "entity_id": {"id": "1"},
            "content": {},
        }
        req = EnrichmentRequest.model_validate(data)
        assert req.task_id == "abc"
