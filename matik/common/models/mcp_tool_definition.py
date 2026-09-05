"""MCP tool definition model."""

from typing import Any

from pydantic import BaseModel, Field


class McpToolDefinition(BaseModel):
    """A single MCP tool derived from an OpenAPI operation.

    Represents one callable tool that the MCP server exposes to clients,
    mapped from an OpenAPI path+operation under /v1/mcp/.
    """

    name: str = Field(..., description="Tool name derived from operationId")
    description: str = Field(
        ..., description="Tool description from summary or description"
    )
    input_schema: dict[str, Any] = Field(
        default_factory=dict, description="JSON Schema for tool parameters"
    )
    method: str = Field(..., description="HTTP method (GET, POST, etc.)")
    path: str = Field(..., description="API path (e.g., /v1/mcp/correlate_incident)")
