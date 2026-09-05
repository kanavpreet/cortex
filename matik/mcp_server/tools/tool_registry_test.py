"""Tests for MCP tool registry."""

import json
from unittest.mock import MagicMock

from common.models.mcp_tool_definition import McpToolDefinition
from mcp_server.tools.tool_registry import McpToolRegistry, parse_openapi_spec

# --- Sample OpenAPI specs for testing ---

SAMPLE_OPENAPI_SPEC: dict[str, object] = {
    "openapi": "3.1.0",
    "info": {"title": "Matik API", "version": "0.1.0"},
    "paths": {
        "/v1/mcp/correlate_incident": {
            "post": {
                "operationId": "correlate_incident",
                "summary": "Correlate an incident with related signals",
                "description": "Finds related alerts, changes, and incidents.",
                "parameters": [
                    {
                        "name": "incident_id",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string"},
                        "description": "The incident ID to correlate",
                    }
                ],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "lookback_minutes": {
                                        "type": "integer",
                                        "description": "Minutes to look back",
                                    }
                                },
                                "required": ["lookback_minutes"],
                            }
                        }
                    },
                },
            }
        },
        "/v1/mcp/summarize_incident": {
            "get": {
                "operationId": "summarize_incident",
                "summary": "Summarize an incident",
                "parameters": [
                    {
                        "name": "incident_id",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
            }
        },
        "/v1/health": {
            "get": {
                "operationId": "health_check",
                "summary": "Health check endpoint",
            }
        },
        "/v1/incidents": {
            "get": {
                "operationId": "list_incidents",
                "summary": "List all incidents",
            }
        },
    },
}


def test_parse_openapi_spec_extracts_mcp_tools() -> None:
    """Test that only /v1/mcp/ paths are parsed into tool definitions."""
    tools = parse_openapi_spec(SAMPLE_OPENAPI_SPEC)

    assert len(tools) == 2
    names = {t.name for t in tools}
    assert names == {"correlate_incident", "summarize_incident"}


def test_parse_openapi_spec_ignores_non_mcp_paths() -> None:
    """Test that non-MCP paths (/v1/health, /v1/incidents) are excluded."""
    tools = parse_openapi_spec(SAMPLE_OPENAPI_SPEC)

    names = {t.name for t in tools}
    assert "health_check" not in names
    assert "list_incidents" not in names


def test_parse_openapi_spec_extracts_correct_fields() -> None:
    """Test that tool fields are correctly extracted from the spec."""
    tools = parse_openapi_spec(SAMPLE_OPENAPI_SPEC)
    tools_by_name = {t.name: t for t in tools}

    correlate = tools_by_name["correlate_incident"]
    assert correlate.method == "POST"
    assert correlate.path == "/v1/mcp/correlate_incident"
    # description is preferred over summary when both are present
    assert correlate.description == "Finds related alerts, changes, and incidents."

    summarize = tools_by_name["summarize_incident"]
    assert summarize.method == "GET"
    assert summarize.path == "/v1/mcp/summarize_incident"
    assert summarize.description == "Summarize an incident"


def test_parse_openapi_spec_builds_input_schema() -> None:
    """Test that input schema merges parameters and request body."""
    tools = parse_openapi_spec(SAMPLE_OPENAPI_SPEC)
    tools_by_name = {t.name: t for t in tools}

    correlate = tools_by_name["correlate_incident"]
    schema = correlate.input_schema

    assert schema["type"] == "object"
    assert "incident_id" in schema["properties"]
    assert "lookback_minutes" in schema["properties"]
    assert "incident_id" in schema["required"]
    assert "lookback_minutes" in schema["required"]


def test_parse_openapi_spec_handles_empty_spec() -> None:
    """Test parsing an empty OpenAPI spec returns no tools."""
    tools = parse_openapi_spec({"openapi": "3.1.0", "paths": {}})
    assert tools == []


def test_parse_openapi_spec_skips_operations_without_operation_id() -> None:
    """Test that operations without operationId are skipped."""
    spec = {
        "paths": {
            "/v1/mcp/no_id": {
                "get": {
                    "summary": "No operation ID",
                }
            }
        }
    }
    tools = parse_openapi_spec(spec)
    assert tools == []


def test_parse_openapi_spec_custom_path_prefix() -> None:
    """Test filtering with a custom path prefix."""
    tools = parse_openapi_spec(SAMPLE_OPENAPI_SPEC, path_prefix="/v1/")
    names = {t.name for t in tools}
    assert "health_check" in names
    assert "list_incidents" in names
    assert "correlate_incident" in names


def test_parse_openapi_spec_prefers_description_over_summary() -> None:
    """Test that description is preferred over summary when both are present."""
    spec = {
        "paths": {
            "/v1/mcp/analyze": {
                "post": {
                    "operationId": "analyze",
                    "summary": "Short summary.",
                    "description": "Analyze incident data for patterns.",
                }
            }
        }
    }
    tools = parse_openapi_spec(spec)
    assert len(tools) == 1
    assert tools[0].description == "Analyze incident data for patterns."


def test_registry_fetch_and_parse() -> None:
    """Test that McpToolRegistry fetches and parses the OpenAPI spec."""
    mock_client = MagicMock()
    mock_client.get_request.return_value = json.dumps(SAMPLE_OPENAPI_SPEC).encode()

    registry = McpToolRegistry(api_client=mock_client)
    tools = registry.fetch_and_parse()

    mock_client.get_request.assert_called_once_with("/openapi.json")
    assert len(tools) == 2
    assert len(registry.tools) == 2


def test_registry_refresh_detects_new_tools() -> None:
    """Test that refresh() detects when new tools are added."""
    mock_client = MagicMock()

    # Initial spec with one tool
    initial_spec = {
        "paths": {
            "/v1/mcp/tool_a": {"get": {"operationId": "tool_a", "summary": "Tool A"}}
        }
    }

    # Updated spec with two tools
    updated_spec = {
        "paths": {
            "/v1/mcp/tool_a": {"get": {"operationId": "tool_a", "summary": "Tool A"}},
            "/v1/mcp/tool_b": {"post": {"operationId": "tool_b", "summary": "Tool B"}},
        }
    }

    mock_client.get_request.side_effect = [
        json.dumps(initial_spec).encode(),
        json.dumps(updated_spec).encode(),
    ]

    registry = McpToolRegistry(api_client=mock_client)
    registry.fetch_and_parse()
    assert len(registry.tools) == 1

    changed = registry.refresh()
    assert changed is True
    assert len(registry.tools) == 2


def test_registry_refresh_detects_removed_tools() -> None:
    """Test that refresh() detects when tools are removed."""
    mock_client = MagicMock()

    initial_spec = {
        "paths": {
            "/v1/mcp/tool_a": {"get": {"operationId": "tool_a", "summary": "Tool A"}},
            "/v1/mcp/tool_b": {"post": {"operationId": "tool_b", "summary": "Tool B"}},
        }
    }

    updated_spec = {
        "paths": {
            "/v1/mcp/tool_a": {"get": {"operationId": "tool_a", "summary": "Tool A"}}
        }
    }

    mock_client.get_request.side_effect = [
        json.dumps(initial_spec).encode(),
        json.dumps(updated_spec).encode(),
    ]

    registry = McpToolRegistry(api_client=mock_client)
    registry.fetch_and_parse()
    assert len(registry.tools) == 2

    changed = registry.refresh()
    assert changed is True
    assert len(registry.tools) == 1


def test_registry_refresh_no_changes() -> None:
    """Test that refresh() returns False when nothing changed."""
    mock_client = MagicMock()

    spec = {
        "paths": {
            "/v1/mcp/tool_a": {"get": {"operationId": "tool_a", "summary": "Tool A"}}
        }
    }

    mock_client.get_request.return_value = json.dumps(spec).encode()

    registry = McpToolRegistry(api_client=mock_client)
    registry.fetch_and_parse()

    changed = registry.refresh()
    assert changed is False
    assert len(registry.tools) == 1


def test_registry_refresh_handles_fetch_failure() -> None:
    """Test that refresh() keeps current tools when fetch fails."""
    mock_client = MagicMock()

    spec = {
        "paths": {
            "/v1/mcp/tool_a": {"get": {"operationId": "tool_a", "summary": "Tool A"}}
        }
    }

    mock_client.get_request.side_effect = [
        json.dumps(spec).encode(),
        RuntimeError("connection refused"),
    ]

    registry = McpToolRegistry(api_client=mock_client)
    registry.fetch_and_parse()
    assert len(registry.tools) == 1

    changed = registry.refresh()
    assert changed is False
    # Tools are preserved
    assert len(registry.tools) == 1
    assert registry.tools[0].name == "tool_a"


def test_registry_refresh_detects_definition_changes() -> None:
    """Test that refresh() detects changes in tool definitions (not just names)."""
    mock_client = MagicMock()

    spec_v1 = {
        "paths": {
            "/v1/mcp/tool_a": {"get": {"operationId": "tool_a", "summary": "Tool A v1"}}
        }
    }

    spec_v2 = {
        "paths": {
            "/v1/mcp/tool_a": {
                "get": {"operationId": "tool_a", "summary": "Tool A v2 - updated"}
            }
        }
    }

    mock_client.get_request.side_effect = [
        json.dumps(spec_v1).encode(),
        json.dumps(spec_v2).encode(),
    ]

    registry = McpToolRegistry(api_client=mock_client)
    registry.fetch_and_parse()

    changed = registry.refresh()
    assert changed is True
    assert registry.tools[0].description == "Tool A v2 - updated"


def test_mcp_tool_definition_model() -> None:
    """Test McpToolDefinition Pydantic model."""
    tool = McpToolDefinition(
        name="test_tool",
        description="A test tool",
        input_schema={"type": "object", "properties": {}},
        method="POST",
        path="/v1/mcp/test_tool",
    )

    assert tool.name == "test_tool"
    assert tool.description == "A test tool"
    assert tool.method == "POST"
    assert tool.path == "/v1/mcp/test_tool"
    assert tool.input_schema == {"type": "object", "properties": {}}


def test_registry_tools_returns_copy() -> None:
    """Test that the tools property returns a copy, not the internal list."""
    mock_client = MagicMock()
    spec = {
        "paths": {
            "/v1/mcp/tool_a": {"get": {"operationId": "tool_a", "summary": "Tool A"}}
        }
    }
    mock_client.get_request.return_value = json.dumps(spec).encode()

    registry = McpToolRegistry(api_client=mock_client)
    registry.fetch_and_parse()

    tools = registry.tools
    tools.clear()
    assert len(registry.tools) == 1  # Internal list is unchanged


def test_registry_get_tool_found() -> None:
    """Test get_tool returns the tool when it exists."""
    mock_client = MagicMock()
    spec = {
        "paths": {
            "/v1/mcp/tool_a": {"get": {"operationId": "tool_a", "summary": "Tool A"}},
            "/v1/mcp/tool_b": {"post": {"operationId": "tool_b", "summary": "Tool B"}},
        }
    }
    mock_client.get_request.return_value = json.dumps(spec).encode()

    registry = McpToolRegistry(api_client=mock_client)
    registry.fetch_and_parse()

    tool = registry.get_tool("tool_b")
    assert tool is not None
    assert tool.name == "tool_b"
    assert tool.method == "POST"


def test_registry_get_tool_not_found() -> None:
    """Test get_tool returns None when tool doesn't exist."""
    mock_client = MagicMock()
    spec = {
        "paths": {
            "/v1/mcp/tool_a": {"get": {"operationId": "tool_a", "summary": "Tool A"}}
        }
    }
    mock_client.get_request.return_value = json.dumps(spec).encode()

    registry = McpToolRegistry(api_client=mock_client)
    registry.fetch_and_parse()

    tool = registry.get_tool("nonexistent")
    assert tool is None
