"""HTTP proxy that forwards MCP tool calls to the Matik API.

The proxy performs mechanical translation from MCP tool call arguments
to HTTP requests. It resolves path parameters, separates query parameters
from request body fields, and forwards the request to the API via
MatikApiClient. No business logic is applied.
"""

import re
from dataclasses import dataclass
from typing import Any

from common.clients.matik_api_client import MatikApiClient
from common.models.mcp_tool_definition import McpToolDefinition
from common.utils import log_utils

logger = log_utils.get_logger(__name__)

# Regex to find path parameters like {incident_id} in API paths
_PATH_PARAM_PATTERN = re.compile(r"\{(\w+)\}")


def _resolve_path(path: str, arguments: dict[str, Any]) -> tuple[str, set[str]]:
    """Substitute path parameters in the URL and return consumed keys.

    Args:
        path: API path with potential {param} placeholders.
        arguments: Tool call arguments dict.

    Returns:
        Tuple of (resolved path, set of argument keys consumed as path params).
    """
    consumed: set[str] = set()

    def _replacer(match: re.Match[str]) -> str:
        param_name = match.group(1)
        if param_name in arguments:
            consumed.add(param_name)
            return str(arguments[param_name])
        return match.group(0)

    resolved = _PATH_PARAM_PATTERN.sub(_replacer, path)
    return resolved, consumed


@dataclass
class ApiProxy:
    """Forwards MCP tool calls to the Matik API as HTTP requests.

    Takes a tool definition and arguments, builds the appropriate HTTP request,
    and returns the raw response bytes. The proxy is stateless — it does not
    cache responses or apply business logic.
    """

    api_client: MatikApiClient

    def forward(
        self,
        tool: McpToolDefinition,
        arguments: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> bytes:
        """Forward a tool call to the Matik API.

        For GET/DELETE requests, remaining arguments (after path param resolution)
        are sent as query parameters. For POST/PUT/PATCH requests, remaining
        arguments are sent as the JSON request body.

        Args:
            tool: The tool definition with method, path, and schema info.
            arguments: The MCP tool call arguments.
            headers: Additional headers to forward (e.g., X-MCP-Session-ID).

        Returns:
            Raw response bytes from the API.

        Raises:
            httpx.HTTPStatusError: If the API returns an error status.
        """
        # Resolve path parameters
        resolved_path, consumed = _resolve_path(tool.path, arguments)
        remaining = {k: v for k, v in arguments.items() if k not in consumed}

        logger.debug(
            "resolved path",
            original=tool.path,
            resolved=resolved_path,
            consumed=consumed,
        )

        logger.info(
            "forwarding tool call",
            tool=tool.name,
            method=tool.method,
            path=resolved_path,
        )

        if tool.method in ("GET", "DELETE"):
            # Send remaining args as query parameters.
            # Preserve list values so httpx serializes them as repeated params
            # (e.g. reference_ids=INC-1&reference_ids=INC-2) rather than
            # converting them to a Python repr string like "['INC-1', 'INC-2']".
            params = (
                {k: v if isinstance(v, list) else str(v) for k, v in remaining.items()}
                if remaining
                else None
            )
            logger.debug(
                "dispatching request",
                method=tool.method,
                path=resolved_path,
                has_params=bool(params),
                has_body=False,
            )
            result = self.api_client.get_request(
                resolved_path, params=params, headers=headers
            )
        else:
            # POST, PUT, PATCH — send remaining args as JSON body
            body = remaining if remaining else None
            logger.debug(
                "dispatching request",
                method=tool.method,
                path=resolved_path,
                has_params=False,
                has_body=bool(body),
            )
            result = self.api_client.json_request(
                tool.method, resolved_path, body=body, headers=headers
            )

        logger.debug("received response", response_bytes=len(result))
        return result
