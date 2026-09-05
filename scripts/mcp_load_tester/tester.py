"""
MCP Load Tester — browser-based interactive load testing for Matik MCP server.

Supports both transport protocols:
  - SSE (Server-Sent Events)     — current production transport
  - Streamable HTTP              — new transport (MCP spec 2025-03-26)

USAGE
-----
From the scripts/ directory (recommended):
    uv run python -m mcp_load_tester

Or via the top-level dispatcher:
    uv run python -m scripts mcp-load-tester

WHAT IT DOES
------------
1. Starts a local HTTP server on a random port.
2. Opens your browser to the load tester UI.
3. The UI lets you configure:
     - MCP server URL + transport (SSE or Streamable HTTP)
     - IAP token for authentication
     - Operation to test (list_tools, call a specific tool, or "mixed" —
       which rotates through list/call/unknown-tool so every Grafana MCP
       dashboard panel except spec-refresh gets data)
     - Load parameters: total calls, concurrency, interval, duration
4. Results stream back to the browser in real-time.
5. Live metrics: completed, success rate, avg/P95 latency, errors, req/s.
6. Latency timeline chart updated as calls complete.
7. Full request log with status + latency per call.
8. Export results as JSON for further analysis.

PREREQUISITES
-------------
- httpx must be installed: cd scripts && uv add httpx
- A running Matik MCP server (sandbox, staging, or production)
- A valid IAP token: gcloud auth print-identity-token
"""

from __future__ import annotations

import json
import queue
import socket
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

try:
    import httpx
except ImportError:
    print(
        "httpx is not installed. Run: cd scripts && uv add httpx",
        file=sys.stderr,
    )
    sys.exit(1)


# ── MCP Protocol Clients ──────────────────────────────────────────────────────


def _strip_bearer(token: str) -> str:
    """Remove 'Bearer ' prefix if present so we always prepend it consistently."""
    token = token.strip()
    if token.lower().startswith("bearer "):
        return token[7:].strip()
    return token


@dataclass
class CallResult:
    call_num: int
    success: bool
    latency_ms: float
    error: str | None = None
    operation: str = ""
    response_summary: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_num": self.call_num,
            "success": self.success,
            "latency_ms": round(self.latency_ms, 2),
            "error": self.error,
            "operation": self.operation,
            "response_summary": self.response_summary,
            "timestamp": self.timestamp,
        }


class _SseSession:
    """MCP session using SSE transport (current production transport).

    Flow:
      1. GET /sse  →  SSE stream; first event is  event:endpoint  with the POST URL.
      2. POST <endpoint>  with JSON-RPC body.
      3. Responses arrive as  event:message  on the SSE stream.
    """

    def __init__(self, base_url: str, raw_token: str, timeout: float) -> None:
        self._url = base_url.rstrip("/")
        self._token = _strip_bearer(raw_token)
        self._timeout = timeout
        self._post_url: str | None = None
        self._resp_qs: dict[Any, queue.Queue[dict[str, Any]]] = {}
        self._mu = threading.Lock()
        self._http = httpx.Client(
            timeout=httpx.Timeout(self._timeout, connect=10.0),
            follow_redirects=True,
        )
        self._closed = False
        self._seq = 1
        self._reader_err: Exception | None = None

    def _hdrs(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def connect(self) -> None:
        ep_q: queue.Queue[str] = queue.Queue()

        def _read() -> None:
            try:
                with self._http.stream(
                    "GET", f"{self._url}/sse", headers=self._hdrs()
                ) as r:
                    r.raise_for_status()
                    evt: str | None = None
                    for raw_line in r.iter_lines():
                        if self._closed:
                            return
                        line = raw_line.strip()
                        if line.startswith("event:"):
                            evt = line[6:].strip()
                        elif line.startswith("data:"):
                            data = line[5:].strip()
                            if evt == "endpoint":
                                path = data
                                full = (
                                    path
                                    if path.startswith("http")
                                    else f"{self._url}{path}"
                                )
                                self._post_url = full
                                ep_q.put(full)
                            elif evt == "message":
                                try:
                                    msg: dict[str, Any] = json.loads(data)
                                    mid = msg.get("id")
                                    with self._mu:
                                        q = self._resp_qs.get(mid)
                                    if q is not None:
                                        q.put(msg)
                                except Exception:
                                    pass
                            evt = None
            except Exception as e:
                self._reader_err = e
                ep_q.put("")

        threading.Thread(target=_read, daemon=True).start()

        try:
            url = ep_q.get(timeout=10.0)
        except queue.Empty:
            raise TimeoutError(
                "SSE connect timeout — no endpoint event received"
            ) from None
        if not url:
            raise self._reader_err or ConnectionError("SSE connection failed")

    def _rpc(self, payload: dict[str, Any], wait: bool = True) -> dict[str, Any]:
        mid = payload.get("id")
        q: queue.Queue[dict[str, Any]] | None = None
        if wait and mid is not None:
            q = queue.Queue()
            with self._mu:
                self._resp_qs[mid] = q
        try:
            r = self._http.post(
                self._post_url,  # type: ignore[arg-type]
                json=payload,
                headers=self._hdrs(),
            )
            r.raise_for_status()
        except Exception:
            if q is not None and mid is not None:
                with self._mu:
                    self._resp_qs.pop(mid, None)
            raise
        if q is not None and mid is not None:
            try:
                return q.get(timeout=self._timeout)
            finally:
                with self._mu:
                    self._resp_qs.pop(mid, None)
        return {}

    def initialize(self) -> dict[str, Any]:
        n = self._seq
        self._seq += 1
        resp = self._rpc(
            {
                "jsonrpc": "2.0",
                "id": n,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "mcp-load-tester", "version": "1.0"},
                },
            }
        )
        # Notification — no id, no response expected
        self._rpc(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            wait=False,
        )
        return resp

    def list_tools(self) -> dict[str, Any]:
        n = self._seq
        self._seq += 1
        return self._rpc({"jsonrpc": "2.0", "id": n, "method": "tools/list"})

    def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        n = self._seq
        self._seq += 1
        return self._rpc(
            {
                "jsonrpc": "2.0",
                "id": n,
                "method": "tools/call",
                "params": {"name": name, "arguments": args},
            }
        )

    def close(self) -> None:
        self._closed = True
        try:
            self._http.close()
        except Exception:
            pass


class _StreamableSession:
    """MCP session using Streamable HTTP transport (MCP spec 2025-03-26).

    Each JSON-RPC message is a plain POST to the MCP URL.  The server responds
    with either application/json or text/event-stream (for streaming responses).
    Session continuity is maintained via the Mcp-Session-Id header.
    """

    def __init__(
        self, base_url: str, raw_token: str, timeout: float, mcp_path: str
    ) -> None:
        self._url = f"{base_url.rstrip('/')}{mcp_path}"
        self._token = _strip_bearer(raw_token)
        self._timeout = timeout
        self._sid: str | None = None
        self._http = httpx.Client(
            timeout=httpx.Timeout(self._timeout, connect=10.0),
            follow_redirects=True,
        )
        self._seq = 1

    def _hdrs(self) -> dict[str, str]:
        h: dict[str, str] = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._sid:
            h["Mcp-Session-Id"] = self._sid
        return h

    def connect(self) -> None:
        pass  # No persistent connection needed for Streamable HTTP

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        r = self._http.post(self._url, json=payload, headers=self._hdrs())
        r.raise_for_status()

        sid = r.headers.get("Mcp-Session-Id")
        if sid:
            self._sid = sid

        if r.status_code == 202 or not r.content:
            return {}

        ct = r.headers.get("content-type", "")
        if "text/event-stream" in ct:
            # Parse SSE-formatted response body
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if data and data != "[DONE]":
                        try:
                            return json.loads(data)  # type: ignore[no-any-return]
                        except Exception:
                            pass
            return {}

        return r.json()  # type: ignore[no-any-return]

    def initialize(self) -> dict[str, Any]:
        n = self._seq
        self._seq += 1
        resp = self._post(
            {
                "jsonrpc": "2.0",
                "id": n,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "mcp-load-tester", "version": "1.0"},
                },
            }
        )
        try:
            # Notification — server may return 202 or empty body
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except Exception:
            pass
        return resp

    def list_tools(self) -> dict[str, Any]:
        n = self._seq
        self._seq += 1
        return self._post({"jsonrpc": "2.0", "id": n, "method": "tools/list"})

    def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        n = self._seq
        self._seq += 1
        return self._post(
            {
                "jsonrpc": "2.0",
                "id": n,
                "method": "tools/call",
                "params": {"name": name, "arguments": args},
            }
        )

    def close(self) -> None:
        if self._sid:
            try:
                self._http.delete(
                    self._url,
                    headers=self._hdrs(),
                )
            except Exception:
                pass
        try:
            self._http.close()
        except Exception:
            pass


def _make_session(
    transport: str,
    base_url: str,
    iap_token: str,
    mcp_path: str,
    timeout: float,
) -> _SseSession | _StreamableSession:
    if transport == "sse":
        return _SseSession(base_url, iap_token, timeout)
    return _StreamableSession(base_url, iap_token, timeout, mcp_path)


# ── Tool argument presets ─────────────────────────────────────────────────────

# Placeholder values keyed by JSON Schema type. These fill a generated preset so
# the user can see the request shape and only has to replace the significant
# values rather than author the whole body by hand.
_PRESET_PLACEHOLDERS: dict[str, Any] = {
    "string": "",
    "integer": 0,
    "number": 0,
    "boolean": False,
    "array": [],
    "object": {},
}


def _placeholder_for(prop_schema: dict[str, Any]) -> Any:
    """Return a placeholder value for a single JSON Schema property.

    Prefers an explicit ``default``, then the first ``enum`` value, then a
    type-based placeholder. Nested objects/arrays recurse so the generated
    preset mirrors the real request shape.
    """
    if "default" in prop_schema:
        return prop_schema["default"]

    enum = prop_schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]

    # JSON Schema "type" may be a list (e.g. ["string", "null"]); pick the first
    # non-null entry so nullable fields still get a usable placeholder.
    raw_type = prop_schema.get("type", "string")
    if isinstance(raw_type, list):
        schema_type = next((t for t in raw_type if t != "null"), "string")
    else:
        schema_type = raw_type

    if schema_type == "object":
        return build_arg_preset(prop_schema)
    if schema_type == "array":
        items = prop_schema.get("items")
        if isinstance(items, dict) and items:
            return [_placeholder_for(items)]
        return []

    return _PRESET_PLACEHOLDERS.get(schema_type, "")


def build_arg_preset(input_schema: dict[str, Any]) -> dict[str, Any]:
    """Build a prefilled argument skeleton from a tool's JSON Schema.

    The MCP server derives each tool's ``inputSchema`` from the Matik API
    OpenAPI spec (path/query params + request body merged into one flat object
    schema — see ``mcp_server/tools/tool_registry.py``). This turns that schema
    into a concrete ``arguments`` object with placeholder values so the load
    tester can present a ready-to-edit body where only the meaningful values
    need to be typed in.

    Required fields are always included. Optional fields are included too (so
    the full shape is visible), but callers can distinguish them via
    ``required_keys`` on the schema if they want to prune.

    Args:
        input_schema: The tool's JSON Schema (an object schema with
            ``properties`` and optional ``required``).

    Returns:
        A dict mapping each property name to a placeholder value. Returns an
        empty dict for no-argument tools or non-object schemas.
    """
    if not isinstance(input_schema, dict):
        return {}

    properties = input_schema.get("properties")
    if not isinstance(properties, dict):
        return {}

    preset: dict[str, Any] = {}
    for name, prop_schema in properties.items():
        if isinstance(prop_schema, dict):
            preset[name] = _placeholder_for(prop_schema)
        else:
            preset[name] = ""
    return preset


# ── Operations ──────────────────────────────────────────────────────────────

# Sub-operations cycled through in "mix" mode so a single run exercises every
# server-side dashboard metric:
#   - "list_tools"   → matik_mcp_active_sessions (the list_tools handler)
#   - "call_tool"    → matik_mcp_tool_calls_total{status="success"} + the
#                      matik_mcp_tool_call_duration_seconds histogram
#   - "call_unknown" → matik_mcp_tool_calls_total{status="error"} +
#                      matik_mcp_tool_call_errors_total{error_type="unknown_tool"}
# (matik_mcp_spec_refreshes_total is emitted by a server-side timer and cannot
# be driven from a client.) The valid call is weighted x2 so successes dominate.
_MIX_ROTATION = ("list_tools", "call_tool", "call_tool", "call_unknown")

# Deliberately non-existent tool name used by the "call_unknown" sub-operation
# to trigger the server's unknown-tool error path.
_UNKNOWN_TOOL_NAME = "load_tester__nonexistent_tool"


def _execute_operation(
    sess: _SseSession | _StreamableSession,
    op: str,
    tool_name: str,
    tool_args: dict[str, Any],
) -> tuple[str, str | None, str]:
    """Run a single MCP operation against an already-initialized session.

    Args:
        sess: Connected, initialized MCP session.
        op: One of "list_tools", "call_tool", or "call_unknown".
        tool_name: Tool to invoke for "call_tool".
        tool_args: Arguments for "call_tool".

    Returns:
        (op_label, error, response_summary) — error is None on success.
    """
    if op == "list_tools":
        resp = sess.list_tools()
        if "error" in resp:
            return "tools/list", str(resp["error"]), ""
        tools = resp.get("result", {}).get("tools", [])
        return "tools/list", None, f"{len(tools)} tool(s) returned"

    # "call_tool" (valid) or "call_unknown" (deliberate unknown-tool error).
    name = _UNKNOWN_TOOL_NAME if op == "call_unknown" else tool_name
    args: dict[str, Any] = {} if op == "call_unknown" else tool_args
    resp = sess.call_tool(name, args)
    if "error" in resp:
        return f"call:{name}", str(resp["error"]), ""

    r = resp.get("result", {})
    if isinstance(r, dict) and r.get("isError"):
        content = r.get("content", [{}])
        txt = (
            content[0].get("text", "Tool returned error")
            if content
            else "Tool returned error"
        )
        return f"call:{name}", str(txt)[:200], ""

    content = r.get("content", []) if isinstance(r, dict) else []
    raw_txt = content[0].get("text", "") if content else str(r)
    summary = (raw_txt[:80] + "…") if len(raw_txt) > 80 else raw_txt
    return f"call:{name}", None, summary


# ── Load Test Engine ──────────────────────────────────────────────────────────


class LoadTester:
    """Runs concurrent MCP calls and broadcasts results to subscribers."""

    def __init__(self) -> None:
        self._running = False
        self._results: list[CallResult] = []
        self._all_events: list[dict[str, Any]] = []  # for replay on late subscribe
        self._mu = threading.Lock()
        self._subscribers: list[queue.Queue[dict[str, Any]]] = []

    def is_running(self) -> bool:
        return self._running

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        """Subscribe to result events.  Replays any events already emitted."""
        q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=5000)
        with self._mu:
            for event in self._all_events:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[dict[str, Any]]) -> None:
        with self._mu:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def _broadcast(self, event: dict[str, Any]) -> None:
        with self._mu:
            self._all_events.append(event)
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass

    def stop(self) -> None:
        self._running = False

    def get_results(self) -> list[dict[str, Any]]:
        with self._mu:
            return [r.to_dict() for r in self._results]

    def run(self, config: dict[str, Any]) -> None:
        """Run load test.  Blocks until all workers finish or stop() is called."""
        transport: str = config.get("transport", "sse")
        base_url: str = config.get("base_url", "").rstrip("/")
        iap_token: str = config.get("iap_token", "")
        mcp_path: str = config.get("mcp_path", "/mcp")
        operation: str = config.get("operation", "list_tools")
        tool_name: str = config.get("tool_name", "")
        tool_args_raw = config.get("tool_args", "{}")
        try:
            tool_args: dict[str, Any] = (
                json.loads(tool_args_raw)
                if isinstance(tool_args_raw, str)
                else tool_args_raw
            )
        except Exception:
            tool_args = {}

        total = max(1, int(config.get("total_calls", 10)))
        concurrency = max(1, min(50, int(config.get("concurrency", 1))))
        interval_ms = max(0, int(config.get("interval_ms", 0)))
        duration_s = float(config.get("duration_seconds", 0))
        timeout = float(config.get("timeout_seconds", 30))

        self._running = True
        with self._mu:
            self._results = []
            self._all_events = []

        self._broadcast({"type": "started", "total": total, "concurrency": concurrency})

        start_wall = time.time()
        call_counter = [0]
        counter_lock = threading.Lock()
        completed_count = [0]

        def _worker() -> None:
            while self._running:
                # Claim the next call number atomically
                with counter_lock:
                    if call_counter[0] >= total:
                        break
                    if duration_s > 0 and (time.time() - start_wall) >= duration_s:
                        self._running = False
                        break
                    n = call_counter[0]
                    call_counter[0] += 1

                t0 = time.monotonic()
                error: str | None = None
                summary = ""
                op_label = operation

                # In "mix" mode each call rotates through a representative set
                # of operations so a single run lights up every dashboard panel
                # (except the server-timer-driven spec-refresh panel).
                sub_op = (
                    _MIX_ROTATION[n % len(_MIX_ROTATION)]
                    if operation == "mix"
                    else operation
                )

                try:
                    sess = _make_session(
                        transport, base_url, iap_token, mcp_path, timeout
                    )
                    sess.connect()
                    sess.initialize()

                    op_label, error, summary = _execute_operation(
                        sess, sub_op, tool_name, tool_args
                    )

                    sess.close()
                except Exception as exc:
                    error = str(exc)[:200]

                latency = (time.monotonic() - t0) * 1000
                result = CallResult(
                    call_num=n + 1,
                    success=error is None,
                    latency_ms=latency,
                    error=error,
                    operation=op_label,
                    response_summary=summary,
                )

                with self._mu:
                    self._results.append(result)
                    completed_count[0] += 1
                    done = completed_count[0]

                self._broadcast(
                    {
                        "type": "result",
                        **result.to_dict(),
                        "completed": done,
                        "total": total,
                    }
                )

                if interval_ms > 0:
                    time.sleep(interval_ms / 1000)

        workers = [
            threading.Thread(target=_worker, daemon=True) for _ in range(concurrency)
        ]
        for w in workers:
            w.start()
        for w in workers:
            w.join()

        self._running = False
        with self._mu:
            done = len(self._results)
        self._broadcast({"type": "done", "completed": done, "total": total})


# ── HTML UI ───────────────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Matik MCP Load Tester</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root { --brand: #FF5A5F; --brand-dark: #e04b50; }
  body { background: #f3f4f6; font-size: 13px; height: 100vh; overflow: hidden; }
  .layout { display: flex; height: 100vh; }
  .sidebar {
    width: 300px; min-width: 300px; background: #fff;
    border-right: 1px solid #e5e7eb; height: 100vh;
    overflow-y: auto; padding: 16px 14px; flex-shrink: 0;
  }
  .main { flex: 1; padding: 16px 20px; overflow-y: auto; height: 100vh; }
  .brand { color: var(--brand); font-weight: 700; font-size: 17px; letter-spacing: -0.3px; }
  .section-hdr {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    color: #9ca3af; letter-spacing: 0.6px;
    margin: 14px 0 6px; border-top: 1px solid #f0f0f0; padding-top: 10px;
  }
  .metric-card {
    background: #fff; border: 1px solid #e5e7eb; border-radius: 8px;
    padding: 12px 10px; text-align: center;
  }
  .metric-val { font-size: 26px; font-weight: 700; color: #111827; line-height: 1.1; }
  .metric-lbl { font-size: 10px; color: #9ca3af; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 2px; }
  .metric-card.ok .metric-val  { color: #059669; }
  .metric-card.err .metric-val { color: #dc2626; }
  .metric-card.blue .metric-val { color: #2563eb; }
  .status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
  .status-dot.running { background: #059669; animation: pulse 1.2s infinite; }
  .status-dot.idle    { background: #d1d5db; }
  .status-dot.error   { background: #dc2626; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.25} }
  .panel { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 14px 16px; }
  .btn-brand { background: var(--brand); border-color: var(--brand); color: #fff; font-weight: 500; }
  .btn-brand:hover { background: var(--brand-dark); border-color: var(--brand-dark); color: #fff; }
  .btn-brand:disabled { opacity: .55; }
  .log-table { font-size: 11px; font-family: 'SFMono-Regular', Consolas, monospace; }
  .log-table tr.ok-row  { background: #f0fdf4; }
  .log-table tr.err-row { background: #fff1f2; }
  .form-label { font-weight: 500; color: #374151; margin-bottom: 3px; }
  .help-text { font-size: 11px; color: #9ca3af; margin-top: 2px; }
  #toolCallFields { display: none; }
  #mcpPathRow    { display: none; }
  .progress-meta { font-size: 11px; color: #9ca3af; text-align: right; }
  .tag-transport {
    font-size: 10px; font-weight: 600; padding: 2px 6px; border-radius: 4px;
    background: #e0f2fe; color: #0369a1; letter-spacing: 0.3px;
  }
</style>
</head>
<body>
<div class="layout">

  <!-- ── Sidebar ────────────────────────────────────────────────────────── -->
  <div class="sidebar">
    <div class="d-flex align-items-center mb-1">
      <span class="brand">Matik MCP</span>
      <span class="ms-2 text-muted" style="font-size:12px">Load Tester</span>
    </div>
    <p class="text-muted mb-0" style="font-size:11px">
      Test your deployed MCP server with configurable load.
    </p>

    <div class="section-hdr">Connection</div>

    <div class="mb-2">
      <label class="form-label">Server URL</label>
      <input id="baseUrl" type="text" class="form-control form-control-sm"
        placeholder="https://mcp-matik-sandbox.a.musta.ch">
    </div>

    <div class="mb-2">
      <label class="form-label">Transport
        <span id="transportTag" class="tag-transport ms-1">SSE</span>
      </label>
      <select id="transport" class="form-select form-select-sm" onchange="onTransportChange()">
        <option value="sse">SSE (current)</option>
        <option value="streamable_http">Streamable HTTP (new)</option>
      </select>
    </div>

    <div id="mcpPathRow" class="mb-2">
      <label class="form-label">MCP Endpoint Path</label>
      <input id="mcpPath" type="text" class="form-control form-control-sm" value="/mcp">
    </div>

    <div class="mb-2">
      <label class="form-label">IAP Token</label>
      <input id="iapToken" type="password" class="form-control form-control-sm"
        placeholder="Paste token (with or without 'Bearer')">
      <div class="help-text mt-1">
        Get a token scoped to the MCP server URL:<br>
        <code id="iapCmd" style="font-size:10px;word-break:break-all;user-select:all">iap-auth &lt;server-url&gt;</code>
      </div>
    </div>

    <button class="btn btn-sm btn-outline-secondary w-100 mb-1" onclick="fetchTools()">
      Fetch Available Tools
    </button>
    <div id="fetchStatus" class="help-text mb-2" style="min-height:16px"></div>

    <div class="section-hdr">Operation</div>

    <div class="mb-2">
      <label class="form-label">What to call</label>
      <select id="operation" class="form-select form-select-sm" onchange="onOperationChange()">
        <option value="list_tools">tools/list  (list available tools)</option>
        <option value="call_tool">tools/call  (call a specific tool)</option>
        <option value="mix">mixed  (exercise all dashboard metrics)</option>
      </select>
      <div id="opHelp" class="help-text mt-1"></div>
    </div>

    <div id="toolCallFields">
      <div class="mb-2">
        <label class="form-label">Tool Name</label>
        <select id="toolName" class="form-select form-select-sm" onchange="onToolChange()">
          <option value="">— fetch tools first —</option>
        </select>
        <div id="toolDesc" class="help-text mt-1" style="min-height:14px"></div>
      </div>
      <div class="mb-2">
        <div class="d-flex justify-content-between align-items-center">
          <label class="form-label mb-0">Arguments (JSON)</label>
          <button type="button" class="btn btn-link btn-sm p-0"
            style="font-size:10px" onclick="resetArgsToPreset()">reset to preset</button>
        </div>
        <textarea id="toolArgs" class="form-control form-control-sm"
          rows="4" style="font-family:monospace;font-size:11px"
          placeholder="{}">{}</textarea>
        <div id="argsHint" class="help-text mt-1"></div>
        <details id="schemaBox" class="mt-1" style="display:none">
          <summary style="font-size:10px;cursor:pointer;color:#6b7280">parameter reference</summary>
          <div id="schemaBody" style="font-size:10px;font-family:monospace;color:#6b7280;
            white-space:pre-wrap;max-height:160px;overflow:auto;margin-top:4px"></div>
        </details>
      </div>
    </div>

    <div class="section-hdr">Load Config</div>

    <div class="row g-2 mb-2">
      <div class="col-6">
        <label class="form-label">Total Calls</label>
        <input id="totalCalls" type="number" class="form-control form-control-sm"
          value="20" min="1" max="10000">
      </div>
      <div class="col-6">
        <label class="form-label">Concurrency</label>
        <input id="concurrency" type="number" class="form-control form-control-sm"
          value="1" min="1" max="50">
        <div class="help-text">parallel workers</div>
      </div>
    </div>

    <div class="row g-2 mb-2">
      <div class="col-6">
        <label class="form-label">Interval (ms)</label>
        <input id="intervalMs" type="number" class="form-control form-control-sm"
          value="0" min="0">
        <div class="help-text">between calls / worker</div>
      </div>
      <div class="col-6">
        <label class="form-label">Timeout (s)</label>
        <input id="timeoutSeconds" type="number" class="form-control form-control-sm"
          value="30" min="5" max="300">
      </div>
    </div>

    <div class="mb-3">
      <label class="form-label">Duration Limit (s)</label>
      <input id="durationSeconds" type="number" class="form-control form-control-sm"
        value="0" min="0">
      <div class="help-text">0 = run until total calls complete</div>
    </div>

    <div class="d-grid gap-2">
      <button id="startBtn" class="btn btn-brand btn-sm" onclick="startTest()">
        ▶&nbsp; Start Load Test
      </button>
      <button id="stopBtn" class="btn btn-sm btn-outline-danger" onclick="stopTest()" disabled>
        ■&nbsp; Stop
      </button>
      <button id="exportBtn" class="btn btn-sm btn-outline-secondary" onclick="exportResults()" disabled>
        ↓&nbsp; Export JSON
      </button>
    </div>
  </div>

  <!-- ── Main ───────────────────────────────────────────────────────────── -->
  <div class="main">

    <!-- Status bar -->
    <div class="d-flex align-items-center mb-2">
      <span id="statusDot" class="status-dot idle"></span>
      <span id="statusText" class="fw-semibold" style="font-size:14px">Idle</span>
      <span id="runInfo" class="ms-auto text-muted" style="font-size:12px"></span>
    </div>

    <!-- Progress -->
    <div class="mb-3">
      <div class="progress" style="height:6px; border-radius:3px">
        <div id="progressBar" class="progress-bar bg-success" style="width:0%;transition:width .15s"></div>
      </div>
      <div id="progressLbl" class="progress-meta">0 / 0</div>
    </div>

    <!-- Metrics: row 1 -->
    <div class="row g-2 mb-2">
      <div class="col">
        <div class="metric-card">
          <div id="mCompleted" class="metric-val">0</div>
          <div class="metric-lbl">Completed</div>
        </div>
      </div>
      <div class="col">
        <div id="cardSuccess" class="metric-card">
          <div id="mSuccessRate" class="metric-val">—</div>
          <div class="metric-lbl">Success</div>
        </div>
      </div>
      <div class="col">
        <div class="metric-card err">
          <div id="mErrors" class="metric-val">0</div>
          <div class="metric-lbl">Errors</div>
        </div>
      </div>
      <div class="col">
        <div class="metric-card blue">
          <div id="mRps" class="metric-val">—</div>
          <div class="metric-lbl">Req / sec</div>
        </div>
      </div>
    </div>

    <!-- Metrics: row 2 -->
    <div class="row g-2 mb-3">
      <div class="col">
        <div class="metric-card">
          <div id="mMin" class="metric-val">—</div>
          <div class="metric-lbl">Min (ms)</div>
        </div>
      </div>
      <div class="col">
        <div class="metric-card blue">
          <div id="mAvg" class="metric-val">—</div>
          <div class="metric-lbl">Avg (ms)</div>
        </div>
      </div>
      <div class="col">
        <div class="metric-card blue">
          <div id="mP95" class="metric-val">—</div>
          <div class="metric-lbl">P95 (ms)</div>
        </div>
      </div>
      <div class="col">
        <div class="metric-card">
          <div id="mMax" class="metric-val">—</div>
          <div class="metric-lbl">Max (ms)</div>
        </div>
      </div>
    </div>

    <!-- Chart -->
    <div class="panel mb-3">
      <div class="d-flex justify-content-between align-items-center mb-2">
        <span class="fw-semibold" style="font-size:13px">Latency Timeline (ms)</span>
        <button class="btn btn-sm btn-outline-secondary py-0 px-2"
          style="font-size:11px" onclick="clearChart()">Clear</button>
      </div>
      <canvas id="chart" height="75"></canvas>
    </div>

    <!-- Log table -->
    <div class="panel">
      <div class="d-flex justify-content-between align-items-center mb-2">
        <span class="fw-semibold" style="font-size:13px">
          Request Log
          <span id="logCount" class="fw-normal text-muted">(0)</span>
        </span>
        <button class="btn btn-sm btn-outline-secondary py-0 px-2"
          style="font-size:11px" onclick="clearLog()">Clear</button>
      </div>
      <div style="max-height:260px;overflow-y:auto">
        <table class="table table-sm table-hover log-table mb-0">
          <thead class="table-light" style="position:sticky;top:0">
            <tr>
              <th style="width:44px">#</th>
              <th style="width:42px">OK</th>
              <th style="width:80px">ms</th>
              <th style="width:140px">Operation</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody id="logBody"></tbody>
        </table>
      </div>
    </div>

  </div><!-- /main -->
</div><!-- /layout -->

<script>
// ── State ──────────────────────────────────────────────────────────────────
let sse = null;
let results = [];
let testStart = null;
let totalTarget = 0;
let chart = null;
let logRowCount = 0;
const MAX_LOG = 500;
const MAX_CHART_PTS = 300;

const chartData = {
  labels: [],
  datasets: [{
    label: 'Latency (ms)',
    data: [],
    borderColor: '#2563eb',
    backgroundColor: 'rgba(37,99,235,0.07)',
    borderWidth: 1.5,
    pointRadius: 2.5,
    pointBackgroundColor: [],
    fill: true,
    tension: 0.3
  }]
};

// ── Init ───────────────────────────────────────────────────────────────────
function updateIapCmd() {
  const url = document.getElementById('baseUrl').value.trim();
  document.getElementById('iapCmd').textContent = url
    ? `iap-auth ${url}`
    : 'iap-auth <server-url>';
}

window.addEventListener('load', () => {
  document.getElementById('baseUrl').addEventListener('input', updateIapCmd);
  onOperationChange();

  const ctx = document.getElementById('chart').getContext('2d');
  chart = new Chart(ctx, {
    type: 'line',
    data: chartData,
    options: {
      animation: false,
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { display: true, title: { display: true, text: 'Call #', font: { size: 11 } } },
        y: { display: true, title: { display: true, text: 'ms', font: { size: 11 } }, beginAtZero: true }
      }
    }
  });
});

// ── UI helpers ─────────────────────────────────────────────────────────────
function onTransportChange() {
  const t = document.getElementById('transport').value;
  const isHttp = t === 'streamable_http';
  document.getElementById('mcpPathRow').style.display = isHttp ? '' : 'none';
  document.getElementById('transportTag').textContent = isHttp ? 'Streamable HTTP' : 'SSE';
}

function onOperationChange() {
  const op = document.getElementById('operation').value;
  // Tool name + args are needed for both an explicit call and the mixed mode
  // (mixed uses them for its successful-call leg).
  document.getElementById('toolCallFields').style.display =
    (op === 'call_tool' || op === 'mix') ? 'block' : 'none';

  const help = {
    list_tools: 'Drives only the Active Sessions panels.',
    call_tool: 'Drives Tool Call Rate, Duration & (on failure) Errors for one tool.',
    mix: 'Rotates tools/list + a valid call + an unknown-tool error so every dashboard panel except Spec Refresh gets data. Pick a no-arg tool (e.g. a health/list tool) below for clean successes.'
  };
  document.getElementById('opHelp').textContent = help[op] || '';
}

function getConfig() {
  return {
    transport:        document.getElementById('transport').value,
    base_url:         document.getElementById('baseUrl').value.trim(),
    iap_token:        document.getElementById('iapToken').value.trim(),
    mcp_path:         document.getElementById('mcpPath').value.trim() || '/mcp',
    operation:        document.getElementById('operation').value,
    tool_name:        document.getElementById('toolName').value,
    tool_args:        document.getElementById('toolArgs').value,
    total_calls:      parseInt(document.getElementById('totalCalls').value) || 20,
    concurrency:      parseInt(document.getElementById('concurrency').value) || 1,
    interval_ms:      parseInt(document.getElementById('intervalMs').value) || 0,
    timeout_seconds:  parseInt(document.getElementById('timeoutSeconds').value) || 30,
    duration_seconds: parseInt(document.getElementById('durationSeconds').value) || 0,
  };
}

// ── Fetch tools ────────────────────────────────────────────────────────────
async function fetchTools() {
  const st = document.getElementById('fetchStatus');
  st.textContent = 'Connecting…';
  st.style.color = '#9ca3af';
  try {
    const r = await fetch('/api/fetch-tools', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(getConfig())
    });
    const d = await r.json();
    if (d.error) {
      st.textContent = '✗ ' + d.error;
      st.style.color = '#dc2626';
      return;
    }
    const sel = document.getElementById('toolName');
    sel.innerHTML = '';
    // Tools may arrive as plain name strings (legacy) or as objects carrying
    // the schema + generated preset. Normalize to objects and cache by name.
    toolsByName = {};
    (d.tools || []).forEach(t => {
      const tool = (typeof t === 'string') ? { name: t } : t;
      toolsByName[tool.name] = tool;
      const o = document.createElement('option');
      o.value = tool.name; o.textContent = tool.name;
      sel.appendChild(o);
    });
    st.textContent = `✓ ${(d.tools||[]).length} tool(s) loaded`;
    st.style.color = '#059669';
    onToolChange();  // prefill args for the first tool in the list
  } catch (e) {
    st.textContent = '✗ ' + String(e);
    st.style.color = '#dc2626';
  }
}

// ── Tool selection: prefill the request body from the tool's schema ──────────
// Populated by fetchTools(); maps tool name → { description, input_schema,
// required, preset }.
let toolsByName = {};

function onToolChange() {
  const name = document.getElementById('toolName').value;
  const tool = toolsByName[name];
  const descEl = document.getElementById('toolDesc');
  const hintEl = document.getElementById('argsHint');
  const box    = document.getElementById('schemaBox');
  const body   = document.getElementById('schemaBody');

  if (!tool) {
    descEl.textContent = '';
    hintEl.textContent = '';
    box.style.display = 'none';
    return;
  }

  descEl.textContent = tool.description || '';

  const preset = tool.preset || {};
  // Auto-fill the body with the generated preset so only real values are typed.
  // Don't clobber edits the user already made for this same tool.
  if (lastPresetTool !== name) {
    document.getElementById('toolArgs').value = JSON.stringify(preset, null, 2);
    lastPresetTool = name;
  }

  const required = tool.required || [];
  const allKeys = Object.keys(preset);
  if (allKeys.length === 0) {
    hintEl.textContent = 'No arguments — this tool takes an empty body {}.';
  } else if (required.length) {
    hintEl.innerHTML = 'Required: ' +
      required.map(k => `<code>${k}</code>`).join(', ');
  } else {
    hintEl.textContent = 'All arguments optional.';
  }

  // Render a compact parameter reference (name: type, required marker).
  const schema = tool.input_schema || {};
  const props = schema.properties || {};
  const lines = Object.keys(props).map(k => {
    const p = props[k] || {};
    let type = p.type || 'string';
    if (Array.isArray(type)) type = type.join('|');
    if (p.enum) type = 'enum[' + p.enum.join(',') + ']';
    const req = required.includes(k) ? '  (required)' : '';
    const desc = p.description ? '  — ' + p.description : '';
    return `${k}: ${type}${req}${desc}`;
  });
  if (lines.length) {
    body.textContent = lines.join('\n');
    box.style.display = '';
  } else {
    box.style.display = 'none';
  }
}

// Tracks which tool the textarea was last auto-filled for, so re-selecting the
// same tool (or re-rendering) doesn't overwrite the user's manual edits.
let lastPresetTool = null;

function resetArgsToPreset() {
  const name = document.getElementById('toolName').value;
  const tool = toolsByName[name];
  if (!tool) return;
  document.getElementById('toolArgs').value =
    JSON.stringify(tool.preset || {}, null, 2);
  lastPresetTool = name;
}

// ── Start / Stop ───────────────────────────────────────────────────────────
async function startTest() {
  const cfg = getConfig();
  if (!cfg.base_url)   { alert('Enter a server URL.'); return; }
  if (!cfg.iap_token) { alert('Enter an IAP token.'); return; }

  results = [];
  testStart = Date.now();
  totalTarget = cfg.total_calls;
  clearChart(); clearLog(); resetMetrics();
  setUI('running');

  // Open SSE stream first so we don't miss early events
  if (sse) { sse.close(); sse = null; }
  sse = new EventSource('/api/stream');
  sse.onmessage = onEvent;
  sse.onerror   = () => setUI('idle');

  try {
    const r = await fetch('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg)
    });
    const d = await r.json();
    if (d.error) {
      setStatus('error', 'Error: ' + d.error);
      setUI('idle');
      if (sse) { sse.close(); sse = null; }
    }
  } catch (e) {
    setStatus('error', 'Failed: ' + e);
    setUI('idle');
    if (sse) { sse.close(); sse = null; }
  }
}

async function stopTest() {
  await fetch('/api/stop', { method: 'POST' });
}

// ── Event handler ──────────────────────────────────────────────────────────
function onEvent(e) {
  let ev;
  try { ev = JSON.parse(e.data); } catch { return; }

  if (ev.type === 'started') {
    totalTarget = ev.total;
    document.getElementById('runInfo').textContent =
      `${ev.concurrency} worker(s)  ·  ${ev.total} calls`;
    return;
  }

  if (ev.type === 'result') {
    results.push(ev);
    updateMetrics(ev);
    addLogRow(ev);
    return;
  }

  if (ev.type === 'done') {
    setStatus('idle', `Done — ${ev.completed} / ${ev.total} calls`);
    setUI('done');
    if (sse) { sse.close(); sse = null; }
  }
}

// ── Metrics ────────────────────────────────────────────────────────────────
function updateMetrics(ev) {
  const total     = ev.total;
  const completed = ev.completed;
  const pct       = total > 0 ? (completed / total * 100) : 0;

  document.getElementById('progressBar').style.width = pct + '%';
  document.getElementById('progressLbl').textContent = `${completed} / ${total}`;
  document.getElementById('mCompleted').textContent  = completed;

  const lats   = results.map(r => r.latency_ms).sort((a, b) => a - b);
  const errors = results.filter(r => !r.success).length;
  const sr     = completed > 0 ? Math.round((completed - errors) / completed * 100) : 100;

  document.getElementById('mErrors').textContent = errors;
  document.getElementById('mSuccessRate').textContent = sr + '%';

  const card = document.getElementById('cardSuccess');
  card.className = 'metric-card ' + (sr >= 99 ? 'ok' : sr >= 90 ? '' : 'err');

  if (lats.length) {
    const avg = lats.reduce((a, b) => a + b, 0) / lats.length;
    const p95 = lats[Math.floor(lats.length * 0.95)] ?? lats[lats.length - 1];
    document.getElementById('mAvg').textContent = Math.round(avg);
    document.getElementById('mP95').textContent = Math.round(p95);
    document.getElementById('mMin').textContent = Math.round(lats[0]);
    document.getElementById('mMax').textContent = Math.round(lats[lats.length - 1]);
  }

  if (testStart) {
    const elapsed = (Date.now() - testStart) / 1000;
    document.getElementById('mRps').textContent =
      elapsed > 0 ? (completed / elapsed).toFixed(1) : '—';
  }

  // Chart point
  const ds = chartData.datasets[0];
  if (chartData.labels.length >= MAX_CHART_PTS) {
    chartData.labels.shift();
    ds.data.shift();
    if (Array.isArray(ds.pointBackgroundColor)) ds.pointBackgroundColor.shift();
  }
  chartData.labels.push(ev.call_num);
  ds.data.push(Math.round(ev.latency_ms));
  if (!Array.isArray(ds.pointBackgroundColor)) ds.pointBackgroundColor = [];
  ds.pointBackgroundColor.push(ev.success ? '#2563eb' : '#dc2626');
  chart.update('none');
}

// ── Log table ──────────────────────────────────────────────────────────────
function addLogRow(ev) {
  const tbody = document.getElementById('logBody');
  if (logRowCount >= MAX_LOG) tbody.removeChild(tbody.firstChild);
  else logRowCount++;

  const tr = document.createElement('tr');
  tr.className = ev.success ? 'ok-row' : 'err-row';

  const icon   = ev.success ? '<span style="color:#059669">✓</span>' : '<span style="color:#dc2626">✗</span>';
  const detail = ev.success
    ? esc(ev.response_summary || '')
    : `<span style="color:#dc2626">${esc(ev.error || '')}</span>`;

  tr.innerHTML = `
    <td>${ev.call_num}</td>
    <td>${icon}</td>
    <td>${Math.round(ev.latency_ms)}</td>
    <td style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(ev.operation||'')}</td>
    <td style="max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${detail}</td>
  `;
  tbody.appendChild(tr);
  document.getElementById('logCount').textContent = `(${logRowCount})`;

  const wrap = tbody.closest('[style*="max-height"]');
  if (wrap) wrap.scrollTop = wrap.scrollHeight;
}

// ── Utilities ──────────────────────────────────────────────────────────────
function esc(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function setStatus(state, text) {
  document.getElementById('statusDot').className  = 'status-dot ' + state;
  document.getElementById('statusText').textContent = text;
}

function setUI(state) {
  const running = state === 'running';
  const done    = state === 'done';
  document.getElementById('startBtn').disabled  = running;
  document.getElementById('stopBtn').disabled   = !running;
  document.getElementById('exportBtn').disabled = !(done || results.length > 0);
  if (running) setStatus('running', 'Running…');
  else if (state === 'idle') setStatus('idle', 'Idle');
}

function resetMetrics() {
  ['mCompleted','mErrors'].forEach(id => document.getElementById(id).textContent = '0');
  ['mSuccessRate','mAvg','mP95','mMin','mMax','mRps'].forEach(id =>
    document.getElementById(id).textContent = '—');
  document.getElementById('progressBar').style.width = '0%';
  document.getElementById('progressLbl').textContent = '0 / 0';
  document.getElementById('runInfo').textContent = '';
}

function clearChart() {
  const ds = chartData.datasets[0];
  chartData.labels = [];
  ds.data = [];
  ds.pointBackgroundColor = [];
  chart.update();
}

function clearLog() {
  document.getElementById('logBody').innerHTML = '';
  logRowCount = 0;
  document.getElementById('logCount').textContent = '(0)';
}

async function exportResults() {
  try {
    const r = await fetch('/api/results');
    const d = await r.json();
    const blob = new Blob([JSON.stringify(d, null, 2)], { type: 'application/json' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = `mcp-load-test-${new Date().toISOString().slice(0,19).replace(/:/g,'-')}.json`;
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) { alert('Export failed: ' + e); }
}
</script>
</body>
</html>"""


# ── Local HTTP Server ─────────────────────────────────────────────────────────

_tester = LoadTester()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        pass  # suppress access logs

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, data: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n) if n else b""
        return json.loads(raw) if raw else {}

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            body = _HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/stream":
            self._stream()
        elif path == "/api/results":
            self._json({"results": _tester.get_results()})
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        if path == "/api/run":
            self._run()
        elif path == "/api/stop":
            _tester.stop()
            self._json({"status": "stopped"})
        elif path == "/api/fetch-tools":
            self._fetch_tools()
        else:
            self.send_error(404)

    def _stream(self) -> None:
        """SSE endpoint that streams load test events to the browser."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self._cors_headers()
        self.end_headers()

        q = _tester.subscribe()
        try:
            while True:
                try:
                    event = q.get(timeout=15.0)
                    msg = f"data: {json.dumps(event)}\n\n"
                    self.wfile.write(msg.encode())
                    self.wfile.flush()
                    if event.get("type") == "done":
                        break
                except queue.Empty:
                    # Keepalive comment to prevent proxy timeouts
                    try:
                        self.wfile.write(b": ka\n\n")
                        self.wfile.flush()
                    except Exception:
                        break
        except Exception:
            pass
        finally:
            _tester.unsubscribe(q)

    def _run(self) -> None:
        config = self._read_json()
        if _tester.is_running():
            self._json({"error": "A test is already running. Stop it first."}, 400)
            return
        threading.Thread(target=_tester.run, args=(config,), daemon=True).start()
        self._json({"status": "started"})

    def _fetch_tools(self) -> None:
        config = self._read_json()
        try:
            sess = _make_session(
                config.get("transport", "sse"),
                config.get("base_url", ""),
                config.get("iap_token", ""),
                config.get("mcp_path", "/mcp"),
                float(config.get("timeout_seconds", 30)),
            )
            sess.connect()
            sess.initialize()
            resp = sess.list_tools()
            sess.close()
            tools = resp.get("result", {}).get("tools", [])
            # Return the full definition (name, description, schema) plus a
            # generated argument preset so the UI can prefill the request body
            # and the user only has to fill in the significant values.
            enriched: list[dict[str, Any]] = []
            for t in tools:
                if not isinstance(t, dict) or "name" not in t:
                    continue
                schema = t.get("inputSchema") or {}
                enriched.append(
                    {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "input_schema": schema,
                        "required": list(schema.get("required", []))
                        if isinstance(schema, dict)
                        else [],
                        "preset": build_arg_preset(schema),
                    }
                )
            self._json({"tools": enriched})
        except Exception as exc:
            self._json({"error": str(exc)}, 500)


# ── Entry Point ───────────────────────────────────────────────────────────────


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return int(s.getsockname()[1])


def main() -> None:
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    url = f"http://localhost:{port}"

    print()
    print("  Matik MCP Load Tester")
    print("  ─────────────────────────────────────────────────────────────")
    print(f"  UI:  {url}")
    print()
    print("  Supports: SSE transport (current) + Streamable HTTP (new)")
    print()
    print("  IAP token — scope it to the MCP server you want to test:")
    print("    iap-auth https://mcp-matik-sandbox.a.musta.ch")
    print()
    print("  Replace the URL with staging or production as needed.")
    print("  The UI will show the exact command once you enter the server URL.")
    print()
    print("  Press Ctrl+C to stop.")
    print()

    threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        server.shutdown()
