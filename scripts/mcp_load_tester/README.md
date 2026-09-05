# MCP Load Tester

Browser-based load testing tool for the Matik MCP server. Supports both SSE (current) and Streamable HTTP (new) transports.

## Quick start

```bash
cd scripts
uv run python -m mcp_load_tester
```

Opens a browser UI at `http://localhost:<random-port>`.

## Usage

1. **Enter the server URL** — e.g. `https://mcp-matik-sandbox.a.musta.ch`
2. **Get an IAP token** scoped to that URL (the command updates as you type):
   ```
   iap-auth https://mcp-matik-sandbox.a.musta.ch
   ```
3. **Choose a transport** — SSE (legacy) or Streamable HTTP (new, preferred)
4. **Fetch Tools** to validate the connection and populate the tool dropdown — runs the full protocol handshake for the selected transport (SSE: `GET /sse` + `POST /messages/`; Streamable HTTP: `POST /mcp`)
5. **Choose an operation** (see below) — `tools/list`, `tools/call`, or `mixed`
   - For `tools/call` / `mixed`, selecting a tool **auto-fills the Arguments
     box with a preset body** derived from that tool's schema (the MCP server
     builds each tool's `inputSchema` from the Matik API OpenAPI spec). You only
     need to replace the placeholder values — required fields are flagged and a
     **parameter reference** (name, type, description) is shown below the box.
     Use **reset to preset** to discard edits and regenerate the skeleton.
6. **Configure load** — total calls, concurrency, interval, timeout
7. **Start** — metrics and a latency chart update in real time
8. **Export JSON** when done

## What it measures (in the browser)

| Metric | Description |
|--------|-------------|
| Success rate | % of calls that completed without error |
| Avg / P95 latency | End-to-end per call (connect → initialize → operation) |
| Req/sec | Throughput across all workers |
| Error log | Per-call status, latency, and error detail |

## Driving the Grafana MCP dashboard

The dashboard panels are emitted by the **MCP server**, not this tool — the
tester just drives traffic. Which server metric fires depends on which
**operation** you select:

| Operation | Server metric(s) emitted | Dashboard panels populated |
|-----------|--------------------------|-----------------------------|
| `tools/list` | `matik_mcp_active_sessions` | Active Sessions only |
| `tools/call` | `matik_mcp_tool_calls_total`, `matik_mcp_tool_call_duration_seconds` (+ `…_errors_total` on failure) | Tool Call Rate, Duration P50/95/99, Errors (if failing) |
| `mixed` | all of the above | Active Sessions, Tool Call Rate, Duration, **and** Errors |

So if you only see **Active Sessions** light up, you're running `tools/list`.
Use **`mixed`** to exercise every panel in a single run: it rotates through
`tools/list`, a valid `tools/call` on the tool you pick (pick a no-arg tool
like a health/list tool for clean successes), and a call to a deliberately
unknown tool (drives `error_type="unknown_tool"` on the Errors panel).

> **Not driver-able from the client:** `matik_mcp_spec_refreshes_total` (the
> Spec Refresh panel) is emitted by a background timer inside the server
> (default every 5 min), so no client traffic can trigger it — just wait for a
> refresh tick or restart the server.

## Prerequisites

```bash
cd scripts && uv sync   # installs httpx and other deps
```
