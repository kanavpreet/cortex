"""MCP tool registry that builds tool definitions from the Matik API OpenAPI spec."""

import json
from dataclasses import dataclass, field
from typing import Any

from common.clients.matik_api_client import MatikApiClient
from common.metrics.mcp_metrics import McpMetrics
from common.models.mcp_tool_definition import McpToolDefinition
from common.utils import log_utils

logger = log_utils.get_logger(__name__)

# Default path prefix for MCP-exposed endpoints
DEFAULT_MCP_PATH_PREFIX = "/v1/mcp/"


def _build_input_schema(
    parameters: list[dict[str, Any]] | None,
    request_body: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a JSON Schema object from OpenAPI parameters and request body.

    Combines path/query parameters and request body into a single flat
    JSON Schema that MCP clients can use to construct tool calls.
    """
    properties: dict[str, Any] = {}
    required: list[str] = []

    # Process path and query parameters
    if parameters:
        for param in parameters:
            param_name = param.get("name", "")
            param_schema = param.get("schema", {"type": "string"})
            param_desc = param.get("description", "")

            prop: dict[str, Any] = {**param_schema}
            if param_desc:
                prop["description"] = param_desc

            properties[param_name] = prop

            if param.get("required", False):
                required.append(param_name)

    # Process request body (JSON content only)
    if request_body:
        content = request_body.get("content", {})
        json_content = content.get("application/json", {})
        body_schema = json_content.get("schema", {})

        # If the body schema has properties, merge them into the top-level
        if "properties" in body_schema:
            properties.update(body_schema["properties"])
            if "required" in body_schema:
                required.extend(body_schema["required"])
        elif body_schema:
            # If it's a non-object schema (e.g., array), wrap it as "body"
            properties["body"] = body_schema
            if request_body.get("required", False):
                required.append("body")

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required

    return schema


def parse_openapi_spec(
    spec: dict[str, Any],
    path_prefix: str = DEFAULT_MCP_PATH_PREFIX,
) -> list[McpToolDefinition]:
    """Parse an OpenAPI spec dict into MCP tool definitions.

    Only paths matching the given prefix are included.

    Args:
        spec: Parsed OpenAPI specification dict.
        path_prefix: Only paths starting with this prefix are included.

    Returns:
        List of McpToolDefinition objects.
    """
    tools: list[McpToolDefinition] = []
    paths = spec.get("paths", {})

    for path, path_item in paths.items():
        if not path.startswith(path_prefix):
            continue

        # path-level parameters apply to all operations
        path_params = path_item.get("parameters", [])

        for method in ("get", "post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if operation is None:
                continue

            operation_id = operation.get("operationId", "")
            if not operation_id:
                # Skip operations without an operationId
                logger.warning(
                    "skipping operation without operationId",
                    path=path,
                    method=method,
                )
                continue

            summary = operation.get("summary", "")
            description = operation.get("description", "")
            tool_description = description or summary or f"{method.upper()} {path}"

            # Merge path-level and operation-level parameters
            op_params = path_params + operation.get("parameters", [])
            request_body = operation.get("requestBody")

            input_schema = _build_input_schema(op_params, request_body)

            tools.append(
                McpToolDefinition(
                    name=operation_id,
                    description=tool_description,
                    input_schema=input_schema,
                    method=method.upper(),
                    path=path,
                )
            )

    return tools


@dataclass
class McpToolRegistry:
    """Registry that fetches the Matik API OpenAPI spec and maintains tool definitions.

    Uses the existing MatikApiClient to fetch /openapi.json with built-in retry logic,
    then parses the spec into McpToolDefinition objects filtered to /v1/mcp/ paths.
    """

    api_client: MatikApiClient
    path_prefix: str = DEFAULT_MCP_PATH_PREFIX
    metrics: McpMetrics | None = None
    _tools: list[McpToolDefinition] = field(default_factory=list, init=False)

    @property
    def tools(self) -> list[McpToolDefinition]:
        """Current list of tool definitions."""
        return list(self._tools)

    def fetch_and_parse(self) -> list[McpToolDefinition]:
        """Fetch the OpenAPI spec from the API and parse it into tool definitions.

        Returns:
            List of discovered McpToolDefinition objects.

        Raises:
            Exception: If the spec cannot be fetched (after MatikApiClient retries).
        """
        logger.info("fetching OpenAPI spec from API")
        raw = self.api_client.get_request("/openapi.json")
        spec = json.loads(raw)
        tools = parse_openapi_spec(spec, self.path_prefix)
        self._tools = tools
        logger.info("parsed tool definitions from OpenAPI spec", tool_count=len(tools))
        return tools

    def refresh(self) -> bool:
        """Re-fetch the spec and update tools if changed.

        Returns:
            True if the tool set changed, False otherwise.
        """
        logger.info("refreshing OpenAPI spec")

        try:
            raw = self.api_client.get_request("/openapi.json")
        except Exception:
            logger.exception("failed to refresh OpenAPI spec, keeping current tools")
            if self.metrics:
                self.metrics.record_spec_refresh("error")
            return False

        spec = json.loads(raw)
        new_tools = parse_openapi_spec(spec, self.path_prefix)

        # Compare by name sets for change detection
        current_names = {t.name for t in self._tools}
        new_names = {t.name for t in new_tools}

        added = new_names - current_names
        removed = current_names - new_names

        if added or removed:
            if added:
                logger.info("new tools discovered", tools=sorted(added))
            if removed:
                logger.info("tools removed", tools=sorted(removed))
            self._tools = new_tools
            if self.metrics:
                self.metrics.record_spec_refresh("success")
            return True

        # Check for definition changes (description, schema, etc.)
        current_by_name = {t.name: t for t in self._tools}
        new_by_name = {t.name: t for t in new_tools}

        for name in current_names:
            if current_by_name[name] != new_by_name[name]:
                logger.info("tool definitions updated")
                self._tools = new_tools
                if self.metrics:
                    self.metrics.record_spec_refresh("success")
                return True

        logger.info("no changes in tool definitions", tool_count=len(self._tools))
        if self.metrics:
            self.metrics.record_spec_refresh("unchanged")
        return False

    def get_tool(self, name: str) -> McpToolDefinition | None:
        """Look up a tool definition by name.

        Args:
            name: The tool name (operationId).

        Returns:
            The McpToolDefinition if found, None otherwise.
        """
        for tool in self._tools:
            if tool.name == name:
                return tool
        return None
