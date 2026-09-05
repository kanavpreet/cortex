"""Unit tests for Greenroom entity models."""

import pytest
from pydantic import ValidationError

from common.models.greenroom_entity import GreenroomEntity, GreenroomEntityRelation


class TestGreenroomEntityRelation:
    """Test suite for GreenroomEntityRelation SQLModel model."""

    def test_valid_instantiation(self) -> None:
        """Test creating GreenroomEntityRelation with valid data."""
        relation = GreenroomEntityRelation(
            type="ownedBy",
            target_ref="group:default/platform-team",
        )
        assert relation.type == "ownedBy"
        assert relation.target_ref == "group:default/platform-team"

    def test_required_fields_missing(self) -> None:
        """Test that missing required fields raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            GreenroomEntityRelation.model_validate({"type": "ownedBy"})
        assert "target_ref" in str(exc_info.value)

    def test_different_relation_types(self) -> None:
        """Test different relationship types."""
        relation_types = ["ownedBy", "dependsOn", "apiProvidedBy", "consumesApi"]
        for rel_type in relation_types:
            relation = GreenroomEntityRelation(
                type=rel_type,
                target_ref="component:default/some-service",
            )
            assert relation.type == rel_type


class TestGreenroomEntity:
    """Test suite for GreenroomEntity SQLModel model."""

    def test_valid_instantiation_minimal(self) -> None:
        """Test creating GreenroomEntity with minimal required fields."""
        entity = GreenroomEntity(
            api_version="backstage.io/v1alpha1",
            kind="component",
            name="my-service",
        )
        assert entity.api_version == "backstage.io/v1alpha1"
        assert entity.kind == "component"
        assert entity.name == "my-service"
        assert entity.id is None  # Default for auto-increment PK
        assert entity.namespace == "default"  # Default
        assert entity.uid is None
        assert entity.etag is None
        assert entity.title is None
        assert entity.description is None
        assert entity.labels is None
        assert entity.annotations is None
        assert entity.tags is None
        assert entity.spec is None
        assert entity.relations is None

    def test_with_all_metadata_fields(self) -> None:
        """Test creating GreenroomEntity with all metadata fields."""
        entity = GreenroomEntity(
            api_version="backstage.io/v1alpha1",
            kind="service",
            name="payment-service",
            namespace="production",
            uid="abc-123-def-456",
            etag="v1.0.0",
            title="Payment Service",
            description="Handles all payment processing",
            labels={"team": "payments", "tier": "critical"},
            annotations={"backstage.io/source-location": "github.com/org/repo"},
            tags=["payments", "critical", "backend"],
        )
        assert entity.namespace == "production"
        assert entity.uid == "abc-123-def-456"
        assert entity.etag == "v1.0.0"
        assert entity.title == "Payment Service"
        assert entity.description == "Handles all payment processing"
        assert entity.labels == {"team": "payments", "tier": "critical"}
        assert entity.annotations == {
            "backstage.io/source-location": "github.com/org/repo"
        }
        assert entity.tags == ["payments", "critical", "backend"]

    def test_with_spec_field(self) -> None:
        """Test creating GreenroomEntity with spec field."""
        entity = GreenroomEntity(
            api_version="backstage.io/v1alpha1",
            kind="component",
            name="my-service",
            spec={
                "type": "service",
                "lifecycle": "production",
                "owner": "team-a",
                "system": "my-system",
            },
        )
        assert entity.spec is not None
        assert entity.spec["type"] == "service"
        assert entity.spec["lifecycle"] == "production"
        assert entity.spec["owner"] == "team-a"

    def test_with_relations(self) -> None:
        """Test creating GreenroomEntity with relations."""
        entity = GreenroomEntity(
            api_version="backstage.io/v1alpha1",
            kind="component",
            name="my-service",
            relations=[
                GreenroomEntityRelation(
                    type="ownedBy",
                    target_ref="group:default/platform-team",
                ),
                GreenroomEntityRelation(
                    type="dependsOn",
                    target_ref="component:default/database",
                ),
            ],
        )
        assert entity.relations is not None
        assert len(entity.relations) == 2
        assert entity.relations[0].type == "ownedBy"
        assert entity.relations[0].target_ref == "group:default/platform-team"
        assert entity.relations[1].type == "dependsOn"

    def test_required_fields_missing(self) -> None:
        """Test that missing required fields raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            GreenroomEntity.model_validate({"api_version": "backstage.io/v1alpha1"})
        error_str = str(exc_info.value)
        assert "kind" in error_str
        assert "name" in error_str

    def test_kind_values(self) -> None:
        """Test different kind values."""
        kinds = ["component", "service", "api", "group", "user", "system", "domain"]
        for kind in kinds:
            entity = GreenroomEntity(
                api_version="backstage.io/v1alpha1",
                kind=kind,
                name=f"test-{kind}",
            )
            assert entity.kind == kind

    def test_default_values(self) -> None:
        """Test that default values are set correctly."""
        entity = GreenroomEntity(
            api_version="backstage.io/v1alpha1",
            kind="component",
            name="test-service",
        )
        assert entity.id is None  # Auto-increment PK defaults to None
        assert entity.namespace == "default"
        assert entity.uid is None
        assert entity.etag is None
        assert entity.title is None
        assert entity.description is None
        assert entity.labels is None
        assert entity.annotations is None
        assert entity.tags is None
        assert entity.spec is None
        assert entity.relations is None

    def test_custom_id(self) -> None:
        """Test that custom id can be set."""
        entity = GreenroomEntity(
            id=42,
            api_version="backstage.io/v1alpha1",
            kind="component",
            name="test-service",
        )
        assert entity.id == 42

    def test_orm_mode(self) -> None:
        """Test that model can be created from dict-like objects."""
        data = {
            "id": 1,
            "api_version": "backstage.io/v1alpha1",
            "kind": "component",
            "name": "my-service",
            "namespace": "production",
            "uid": "uid-123",
            "etag": "v1",
            "title": "My Service",
            "description": "Test service",
            "labels": {"env": "prod"},
            "annotations": {"key": "value"},
            "tags": ["tag1", "tag2"],
            "spec": {"type": "service"},
            "relations": [{"type": "ownedBy", "target_ref": "group:default/team"}],
        }
        entity = GreenroomEntity.model_validate(data)
        assert entity.id == 1
        assert entity.api_version == "backstage.io/v1alpha1"
        assert entity.kind == "component"
        assert entity.name == "my-service"
        assert entity.namespace == "production"
        assert entity.labels == {"env": "prod"}
        assert entity.relations is not None
        assert len(entity.relations) == 1
        assert entity.relations[0].type == "ownedBy"
