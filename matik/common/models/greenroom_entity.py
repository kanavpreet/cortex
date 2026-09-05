"""Greenroom/Backstage catalog entity models using SQLModel."""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, String, Text, text
from sqlalchemy.dialects.mysql import JSON
from sqlmodel import Field, SQLModel


class GreenroomEntityRelation(SQLModel):
    """
    Represents a relationship between catalog entities.

    Note: This is a nested model (no table=True), used within GreenroomEntity.
    """

    # The type of relationship
    type: str = Field(
        ...,
        description="Relationship type: ownedBy, dependsOn, apiProvidedBy, etc.",
    )

    # Reference to the target entity in the format "kind:namespace/name"
    target_ref: str = Field(
        ...,
        description="Target entity ref: kind:namespace/name",
    )


class GreenroomEntity(SQLModel, table=True):
    """
    Represents a Backstage/Greenroom catalog entity.

    Maps to: greenroom_entities table (future)

    This model flattens the nested structure from the Greenroom API
    for easier use within Matik.
    """

    __tablename__ = "greenroom_entities"
    __table_args__ = {"extend_existing": True}

    # Primary key
    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
        description="Auto-increment database ID",
    )

    #
    # Core Entity Fields
    #

    # The version of specification format for this entity
    api_version: str = Field(
        sa_column=Column(String(100), nullable=False),
        description="Spec version, e.g. backstage.io/v1alpha1",
    )

    # The high level entity type being described
    kind: str = Field(
        sa_column=Column(String(50), nullable=False),
        description="Entity type: component, service, api, group, or user",
    )

    #
    # Metadata Fields (flattened from EntityMeta)
    #

    # The name of the entity
    name: str = Field(
        sa_column=Column(String(255), nullable=False),
        description="Entity name, unique within namespace+kind",
    )

    # The namespace that the entity belongs to
    namespace: str | None = Field(
        default="default",
        sa_column=Column(String(255), nullable=True),
        description="Entity namespace, defaults to default",
    )

    # A globally unique ID for the entity
    uid: str | None = Field(
        default=None,
        sa_column=Column(String(255), nullable=True),
        description="Globally unique ID assigned by Greenroom",
    )

    # An opaque string that changes for each update operation
    etag: str | None = Field(
        default=None,
        sa_column=Column(String(255), nullable=True),
        description="Optimistic concurrency control token",
    )

    # A human-readable display name for the entity
    title: str | None = Field(
        default=None,
        sa_column=Column(String(500), nullable=True),
        description="Human-readable display name",
    )

    # A short description of the entity
    description: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="Short description, typically one line",
    )

    # Key-value pairs for classifying and organizing entities
    labels: dict[str, str] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description="Key-value labels for classification",
    )

    # Key-value pairs for tool-specific metadata and integrations
    annotations: dict[str, str] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description="Tool-specific metadata and integrations",
    )

    # Single-valued strings for categorizing entities
    tags: list[str] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description="Categorization tags",
    )

    #
    # Entity Specification (entity type-specific data)
    #

    # Entity-specific data structure, varies by Kind
    spec: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description="Entity-specific data, structure varies by kind",
    )

    #
    # Relations (references to other entities)
    #

    # Relationships this entity has with other entities
    relations: list[GreenroomEntityRelation] | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description="Relationships with other entities",
    )

    #
    # Row-level audit timestamps (DB-managed)
    #

    # When this DB row was written / last updated.
    row_created_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
        ),
        description="DB row creation timestamp (UTC, DB-managed)",
    )
    row_updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        ),
        description="DB row last-update timestamp (UTC, DB-managed)",
    )
