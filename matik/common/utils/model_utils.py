"""Model utilities for deriving Pydantic response models from SQLModel table models."""

from typing import Any, get_args

from pydantic import BaseModel, ConfigDict, create_model
from sqlmodel import SQLModel


def make_response_model(
    base: type[SQLModel],
    name: str,
    **extra_fields: Any,
) -> type[BaseModel]:
    """Derive a pure Pydantic response model from a SQLModel table model.

    SQLModel's metaclass processes SQLAlchemy column types on all inherited
    fields, which breaks non-table subclasses for fields like ``list[str]``
    (JSON columns). This function uses Pydantic's ``create_model`` instead,
    which bypasses SQLModel's metaclass entirely and treats the inherited
    fields as plain Pydantic fields.

    SQLModel also allows non-optional Python annotations (e.g. ``datetime``)
    paired with ``nullable=True`` SA columns. Those fields can be absent from
    ``model_dump()`` when not explicitly set, which would cause a pure Pydantic
    model to reject construction. This function detects that pattern and
    promotes such fields to ``Optional`` with a ``None`` default so that the
    response model can always be constructed from a SQLModel instance.

    Args:
        base: The SQLModel table model to derive fields from.
        name: The class name for the new response model.
        **extra_fields: Additional fields as ``(annotation, FieldInfo | default)``
            tuples, forwarded directly to ``create_model``.
            Example: ``url=(str, Field(..., description="..."))``

    Returns:
        A pure Pydantic ``BaseModel`` subclass with all fields from ``base``
        plus any extra fields, configured with ``from_attributes=True``.
    """
    fields: dict[str, Any] = {}
    for field_name, fi in base.model_fields.items():
        annotation = fi.annotation

        # If the SA column is nullable but the Python annotation doesn't
        # include None, promote to Optional[annotation] with a None default.
        # This reconciles SQLModel's pattern of pairing non-optional Python
        # types with nullable SA columns.
        sa_column = getattr(fi, "sa_column", None)
        is_sa_nullable = sa_column is not None and getattr(sa_column, "nullable", False)
        already_optional = type(None) in get_args(annotation)

        if is_sa_nullable and not already_optional and annotation is not None:
            fields[field_name] = (annotation | None, None)
        else:
            fields[field_name] = (annotation, fi)

    fields.update(extra_fields)
    return create_model(
        name,
        __config__=ConfigDict(from_attributes=True),
        **fields,
    )
