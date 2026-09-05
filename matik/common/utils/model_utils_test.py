"""Tests for model_utils."""

from datetime import datetime
from typing import get_args

import pytest
from pydantic import BaseModel, Field

from common.models.incidentio_incident import IncidentIOIncident
from common.utils.model_utils import make_response_model


@pytest.fixture
def sample_incident() -> IncidentIOIncident:
    return IncidentIOIncident(
        incident_id="01K3H5K30V3TECAF9G2HD1X5ZB",
        reference_id="INC-99",
        severity="Sev-1",
        slack_channel_id="C123",
        status="open",
        visibility="public",
        created_at=datetime(2024, 1, 15, 10, 30, 0),
        reported_at=datetime(2024, 1, 15, 10, 30, 0),
        affected_services=["svc-a", "svc-b"],
    )


class TestMakeResponseModel:
    def test_returns_pydantic_base_model(self) -> None:
        model_cls = make_response_model(IncidentIOIncident, "TestModel")
        assert issubclass(model_cls, BaseModel)

    def test_inherits_all_fields_from_base(self) -> None:
        model_cls = make_response_model(IncidentIOIncident, "TestModel")
        for field_name in IncidentIOIncident.model_fields:
            assert field_name in model_cls.model_fields

    def test_extra_fields_are_added(self) -> None:
        model_cls = make_response_model(
            IncidentIOIncident,
            "TestModel",
            url=(str, Field(..., description="Source URL")),
        )
        assert "url" in model_cls.model_fields

    def test_can_be_constructed_from_sqlmodel_instance(
        self, sample_incident: IncidentIOIncident
    ) -> None:
        model_cls = make_response_model(
            IncidentIOIncident,
            "TestModel",
            url=(str, Field(..., description="Source URL")),
        )
        instance = model_cls(
            **sample_incident.model_dump(), url="https://example.com/99"
        )
        assert instance.reference_id == "INC-99"  # type: ignore[attr-defined]
        assert instance.url == "https://example.com/99"  # type: ignore[attr-defined]
        assert instance.affected_services == ["svc-a", "svc-b"]  # type: ignore[attr-defined]

    def test_from_attributes_is_enabled(self) -> None:
        model_cls = make_response_model(IncidentIOIncident, "TestModel")
        assert model_cls.model_config.get("from_attributes") is True

    def test_sa_nullable_fields_are_promoted_to_optional(self) -> None:
        """Fields with nullable SA columns but non-optional Python annotations
        should become Optional with a None default so that response model
        construction succeeds even when model_dump() omits them."""
        model_cls = make_response_model(IncidentIOIncident, "TestModel")
        # updated_at is typed datetime (non-optional) but sa_column(nullable=True)
        fi = model_cls.model_fields["updated_at"]
        assert type(None) in get_args(fi.annotation)
        assert fi.default is None

    def test_nullable_field_absent_from_dump_uses_none_default(
        self, sample_incident: IncidentIOIncident
    ) -> None:
        """When model_dump() omits a sa-nullable field, the response model
        should fall back to None rather than raising a validation error."""
        model_cls = make_response_model(IncidentIOIncident, "TestModel")
        dump = sample_incident.model_dump()
        # Simulate the field being absent (as SQLModel does for unset nullable cols)
        dump.pop("updated_at", None)
        instance = model_cls(**dump)
        assert instance.updated_at is None  # type: ignore[attr-defined]
