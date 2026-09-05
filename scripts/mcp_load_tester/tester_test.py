"""Unit tests for mcp_load_tester.tester."""

from __future__ import annotations

import io
import queue
from typing import Any
from unittest.mock import MagicMock

from . import tester


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        content: bytes = b"{}",
        headers: dict[str, str] | None = None,
        text: str = "",
        json_body: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self.text = text
        self._json_body = json_body or {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._json_body


class _FakeSession:
    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.response = response or {"result": {"tools": [{"name": "alpha"}]}}
        self.closed = False
        self.called_tool_name: str | None = None
        self.called_tool_args: dict[str, Any] | None = None

    def connect(self) -> None:
        return None

    def initialize(self) -> dict[str, Any]:
        return {"result": {"ok": True}}

    def list_tools(self) -> dict[str, Any]:
        return self.response

    def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.called_tool_name = name
        self.called_tool_args = args
        return self.response

    def close(self) -> None:
        self.closed = True


class _ImmediateThread:
    def __init__(
        self,
        *,
        target: Any = None,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
        daemon: bool | None = None,
    ) -> None:
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}
        self.daemon = daemon

    def start(self) -> None:
        if self._target is not None:
            self._target(*self._args, **self._kwargs)

    def join(self) -> None:
        return None


class _FakeStreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def __enter__(self) -> _FakeStreamResponse:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self) -> list[str]:
        return self._lines


class TestHelpers:
    def test_strip_bearer_prefix_case_insensitive(self) -> None:
        assert tester._strip_bearer("  Bearer token-123 ") == "token-123"
        assert tester._strip_bearer("bearer token-abc") == "token-abc"

    def test_strip_bearer_leaves_token_when_no_prefix(self) -> None:
        assert tester._strip_bearer("plain-token") == "plain-token"

    def test_call_result_to_dict_rounds_latency(self) -> None:
        result = tester.CallResult(call_num=1, success=True, latency_ms=12.3456)
        as_dict = result.to_dict()

        assert as_dict["call_num"] == 1
        assert as_dict["success"] is True
        assert as_dict["latency_ms"] == 12.35


class TestStreamableSession:
    def test_hdrs_includes_session_id_when_present(self) -> None:
        sess = tester._StreamableSession(
            "https://example.com", "Bearer abc", 30, "/mcp"
        )
        sess._sid = "sid-123"

        headers = sess._hdrs()

        assert headers["Authorization"] == "Bearer abc"
        assert headers["Mcp-Session-Id"] == "sid-123"

    def test_post_handles_202_no_content(self) -> None:
        sess = tester._StreamableSession("https://example.com", "token", 30, "/mcp")
        sess._http = MagicMock()
        sess._http.post.return_value = _FakeResponse(
            status_code=202,
            content=b"",
            headers={"Mcp-Session-Id": "sid-1"},
        )

        response = sess._post({"jsonrpc": "2.0", "method": "ping"})

        assert response == {}
        assert sess._sid == "sid-1"

    def test_post_parses_sse_data_line(self) -> None:
        sess = tester._StreamableSession("https://example.com", "token", 30, "/mcp")
        sess._http = MagicMock()
        sess._http.post.return_value = _FakeResponse(
            headers={"content-type": "text/event-stream"},
            text='event: message\ndata: {"result": {"ok": true}}\n\n',
            content=b"event-stream",
        )

        response = sess._post({"jsonrpc": "2.0", "method": "ping"})

        assert response == {"result": {"ok": True}}

    def test_connect_noop(self) -> None:
        sess = tester._StreamableSession("https://example.com", "token", 30, "/mcp")
        sess.connect()

    def test_post_sse_invalid_data_returns_empty(self) -> None:
        sess = tester._StreamableSession("https://example.com", "token", 30, "/mcp")
        sess._http = MagicMock()
        sess._http.post.return_value = _FakeResponse(
            headers={"content-type": "text/event-stream"},
            text="data: not-json\n\n",
            content=b"event-stream",
        )

        response = sess._post({"jsonrpc": "2.0", "method": "ping"})

        assert response == {}

    def test_initialize_list_and_call_tool(self, monkeypatch: Any) -> None:
        sess = tester._StreamableSession("https://example.com", "token", 30, "/mcp")
        sent: list[dict[str, Any]] = []

        def _fake_post(payload: dict[str, Any]) -> dict[str, Any]:
            sent.append(payload)
            if payload.get("method") == "initialize":
                return {"result": {"server": "ok"}}
            return {}

        monkeypatch.setattr(sess, "_post", _fake_post)

        init_resp = sess.initialize()
        list_resp = sess.list_tools()
        call_resp = sess.call_tool("echo", {"x": 1})

        assert init_resp == {"result": {"server": "ok"}}
        assert list_resp == {}
        assert call_resp == {}
        assert sent[0]["method"] == "initialize"
        assert sent[1]["method"] == "notifications/initialized"
        assert sent[2]["method"] == "tools/list"
        assert sent[3]["method"] == "tools/call"

    def test_close_sends_delete_when_session_id_present(self) -> None:
        sess = tester._StreamableSession("https://example.com", "token", 30, "/mcp")
        sess._http = MagicMock()
        sess._sid = "sid-abc"

        sess.close()

        sess._http.delete.assert_called_once()
        call_kwargs = sess._http.delete.call_args
        assert call_kwargs[0][0] == "https://example.com/mcp"
        assert call_kwargs[1]["headers"]["Mcp-Session-Id"] == "sid-abc"

    def test_close_skips_delete_when_no_session_id(self) -> None:
        sess = tester._StreamableSession("https://example.com", "token", 30, "/mcp")
        sess._http = MagicMock()

        sess.close()

        sess._http.delete.assert_not_called()
        sess._http.close.assert_called_once()

    def test_close_swallow_exception(self) -> None:
        sess = tester._StreamableSession("https://example.com", "token", 30, "/mcp")
        sess._http = MagicMock()
        sess._http.close.side_effect = RuntimeError("close failed")

        sess.close()

        assert sess._http.close.called

    def test_initialize_swallow_notification_exception(self, monkeypatch: Any) -> None:
        sess = tester._StreamableSession("https://example.com", "token", 30, "/mcp")

        def _fake_post(payload: dict[str, Any]) -> dict[str, Any]:
            if payload.get("method") == "notifications/initialized":
                raise RuntimeError("notify failed")
            return {"result": {"ok": True}}

        monkeypatch.setattr(sess, "_post", _fake_post)

        resp = sess.initialize()

        assert resp == {"result": {"ok": True}}


class TestSseSession:
    def test_connect_sets_post_url(self, monkeypatch: Any) -> None:
        sess = tester._SseSession("https://example.com", "token", 30)
        sess._http = MagicMock()
        sess._http.stream.return_value = _FakeStreamResponse(
            ["event: endpoint", "data: /messages"]
        )
        monkeypatch.setattr("mcp_load_tester.tester.threading.Thread", _ImmediateThread)

        sess.connect()

        assert sess._post_url == "https://example.com/messages"

    def test_connect_raises_reader_error(self, monkeypatch: Any) -> None:
        sess = tester._SseSession("https://example.com", "token", 30)
        sess._http = MagicMock()
        sess._http.stream.side_effect = RuntimeError("boom")
        monkeypatch.setattr("mcp_load_tester.tester.threading.Thread", _ImmediateThread)

        try:
            sess.connect()
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "boom" in str(exc)

    def test_connect_routes_message_event_to_registered_queue(
        self, monkeypatch: Any
    ) -> None:
        sess = tester._SseSession("https://example.com", "token", 30)
        sess._http = MagicMock()
        sess._resp_qs[7] = queue.Queue()
        sess._http.stream.return_value = _FakeStreamResponse(
            [
                "event: message",
                'data: {"id": 7, "result": {"ok": true}}',
                "event: endpoint",
                "data: /messages",
            ]
        )
        monkeypatch.setattr("mcp_load_tester.tester.threading.Thread", _ImmediateThread)

        sess.connect()

        msg = sess._resp_qs[7].get(timeout=0.1)
        assert msg["id"] == 7
        assert msg["result"]["ok"] is True

    def test_connect_ignores_bad_message_json(self, monkeypatch: Any) -> None:
        sess = tester._SseSession("https://example.com", "token", 30)
        sess._http = MagicMock()
        sess._resp_qs[1] = queue.Queue()
        sess._http.stream.return_value = _FakeStreamResponse(
            [
                "event: message",
                "data: not-json",
                "event: endpoint",
                "data: /messages",
            ]
        )
        monkeypatch.setattr("mcp_load_tester.tester.threading.Thread", _ImmediateThread)

        sess.connect()

        assert sess._resp_qs[1].empty()

    def test_connect_returns_early_when_closed(self, monkeypatch: Any) -> None:
        sess = tester._SseSession("https://example.com", "token", 30)
        sess._closed = True
        sess._http = MagicMock()
        sess._http.stream.return_value = _FakeStreamResponse(["event: endpoint"])
        monkeypatch.setattr("mcp_load_tester.tester.threading.Thread", _ImmediateThread)

        try:
            sess.connect()
            assert False, "expected TimeoutError"
        except TimeoutError:
            pass

    def test_rpc_waits_for_message_and_cleans_queue(self) -> None:
        sess = tester._SseSession("https://example.com", "token", 30)
        sess._post_url = "https://example.com/messages"
        sess._http = MagicMock()

        def _post(*args: Any, **kwargs: Any) -> _FakeResponse:
            _ = (args, kwargs)
            sess._resp_qs[1].put({"id": 1, "result": {"ok": True}})
            return _FakeResponse()

        sess._http.post.side_effect = _post

        resp = sess._rpc({"jsonrpc": "2.0", "id": 1, "method": "ping"})

        assert resp == {"id": 1, "result": {"ok": True}}
        assert 1 not in sess._resp_qs

    def test_rpc_post_error_cleans_queue(self) -> None:
        sess = tester._SseSession("https://example.com", "token", 30)
        sess._post_url = "https://example.com/messages"
        sess._http = MagicMock()
        sess._http.post.side_effect = RuntimeError("post failed")

        try:
            sess._rpc({"jsonrpc": "2.0", "id": 9, "method": "ping"})
            assert False, "expected RuntimeError"
        except RuntimeError:
            assert 9 not in sess._resp_qs

    def test_rpc_returns_empty_when_wait_disabled(self) -> None:
        sess = tester._SseSession("https://example.com", "token", 30)
        sess._post_url = "https://example.com/messages"
        sess._http = MagicMock()
        sess._http.post.return_value = _FakeResponse()

        resp = sess._rpc({"jsonrpc": "2.0", "method": "notify"}, wait=False)

        assert resp == {}

    def test_initialize_list_and_call_tool(self, monkeypatch: Any) -> None:
        sess = tester._SseSession("https://example.com", "token", 30)
        sent: list[tuple[dict[str, Any], bool]] = []

        def _fake_rpc(payload: dict[str, Any], wait: bool = True) -> dict[str, Any]:
            sent.append((payload, wait))
            if payload.get("method") == "initialize":
                return {"result": {"ok": True}}
            return {}

        monkeypatch.setattr(sess, "_rpc", _fake_rpc)

        init_resp = sess.initialize()
        list_resp = sess.list_tools()
        call_resp = sess.call_tool("echo", {"x": 2})

        assert init_resp == {"result": {"ok": True}}
        assert list_resp == {}
        assert call_resp == {}
        assert sent[0][0]["method"] == "initialize"
        assert sent[1][0]["method"] == "notifications/initialized"
        assert sent[1][1] is False
        assert sent[2][0]["method"] == "tools/list"
        assert sent[3][0]["method"] == "tools/call"

    def test_close_swallow_exception(self) -> None:
        sess = tester._SseSession("https://example.com", "token", 30)
        sess._http = MagicMock()
        sess._http.close.side_effect = RuntimeError("close failed")

        sess.close()

        assert sess._closed is True


class TestFactory:
    def test_make_session_returns_sse_session(self) -> None:
        session = tester._make_session(
            "sse", "https://example.com", "token", "/mcp", 30
        )
        assert isinstance(session, tester._SseSession)

    def test_make_session_returns_streamable_session(self) -> None:
        session = tester._make_session(
            "streamable_http", "https://example.com", "token", "/mcp", 30
        )
        assert isinstance(session, tester._StreamableSession)


class TestLoadTester:
    def test_subscribe_replays_existing_events(self) -> None:
        load_tester = tester.LoadTester()
        load_tester._broadcast({"type": "started"})

        q = load_tester.subscribe()

        replayed = q.get(timeout=0.1)
        assert replayed["type"] == "started"

    def test_run_list_tools_records_success(self, monkeypatch: Any) -> None:
        fake_sess = _FakeSession(response={"result": {"tools": [{}, {}]}})
        monkeypatch.setattr(tester, "_make_session", lambda *args, **kwargs: fake_sess)

        load_tester = tester.LoadTester()
        load_tester.run(
            {
                "operation": "list_tools",
                "total_calls": 2,
                "concurrency": 1,
                "base_url": "https://example.com",
                "iap_token": "token",
            }
        )

        results = load_tester.get_results()
        assert len(results) == 2
        assert all(r["success"] for r in results)
        assert all(r["operation"] == "tools/list" for r in results)

    def test_run_call_tool_uses_parsed_json_args(self, monkeypatch: Any) -> None:
        fake_sess = _FakeSession(response={"result": {"content": [{"text": "ok"}]}})
        monkeypatch.setattr(tester, "_make_session", lambda *args, **kwargs: fake_sess)

        load_tester = tester.LoadTester()
        load_tester.run(
            {
                "operation": "call_tool",
                "tool_name": "echo",
                "tool_args": '{"limit": 5}',
                "total_calls": 1,
                "concurrency": 1,
                "base_url": "https://example.com",
                "iap_token": "token",
            }
        )

        assert fake_sess.called_tool_name == "echo"
        assert fake_sess.called_tool_args == {"limit": 5}
        results = load_tester.get_results()
        assert len(results) == 1
        assert results[0]["success"] is True

    def test_run_call_tool_marks_is_error_as_failure(self, monkeypatch: Any) -> None:
        fake_sess = _FakeSession(
            response={
                "result": {
                    "isError": True,
                    "content": [{"text": "backend failed"}],
                }
            }
        )
        monkeypatch.setattr(tester, "_make_session", lambda *args, **kwargs: fake_sess)

        load_tester = tester.LoadTester()
        load_tester.run(
            {
                "operation": "call_tool",
                "tool_name": "explode",
                "tool_args": "{}",
                "total_calls": 1,
                "concurrency": 1,
                "base_url": "https://example.com",
                "iap_token": "token",
            }
        )

        result = load_tester.get_results()[0]
        assert result["success"] is False
        assert result["operation"] == "call:explode"
        assert "backend failed" in (result["error"] or "")

    def test_run_mix_rotates_operations_and_hits_unknown_tool(
        self, monkeypatch: Any
    ) -> None:
        """Mixed mode cycles list_tools, valid calls, and an unknown-tool call.

        Over one full rotation the tester must drive tools/list (active
        sessions), a valid tools/call (success + duration), and a call to the
        deliberately-unknown tool (error) so every dashboard panel gets data.
        """
        called_names: list[str] = []

        class _RecordingSession(_FakeSession):
            def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
                called_names.append(name)
                return {"result": {"content": [{"text": "ok"}]}}

            def list_tools(self) -> dict[str, Any]:
                return {"result": {"tools": [{"name": "alpha"}]}}

        monkeypatch.setattr(
            tester, "_make_session", lambda *args, **kwargs: _RecordingSession()
        )

        load_tester = tester.LoadTester()
        load_tester.run(
            {
                "operation": "mix",
                "tool_name": "echo",
                "tool_args": "{}",
                "total_calls": len(tester._MIX_ROTATION),
                "concurrency": 1,
                "base_url": "https://example.com",
                "iap_token": "token",
            }
        )

        results = load_tester.get_results()
        assert len(results) == len(tester._MIX_ROTATION)
        ops = {r["operation"] for r in results}
        # One full rotation touches tools/list, the valid tool, and the unknown tool.
        assert "tools/list" in ops
        assert "call:echo" in ops
        assert f"call:{tester._UNKNOWN_TOOL_NAME}" in ops
        assert tester._UNKNOWN_TOOL_NAME in called_names
        assert "echo" in called_names

    def test_unsubscribe_non_member_queue_is_noop(self) -> None:
        load_tester = tester.LoadTester()
        q: queue.Queue[dict[str, Any]] = queue.Queue()

        load_tester.unsubscribe(q)

        assert load_tester.is_running() is False

    def test_broadcast_ignores_full_subscriber_queue(self) -> None:
        load_tester = tester.LoadTester()
        full_q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        full_q.put({"existing": True})
        load_tester._subscribers.append(full_q)

        load_tester._broadcast({"type": "result", "call_num": 1})

        assert load_tester._all_events[-1]["type"] == "result"

    def test_subscribe_handles_replay_queue_full(self) -> None:
        load_tester = tester.LoadTester()
        load_tester._all_events = [{"i": i} for i in range(6001)]

        q = load_tester.subscribe()

        assert q.qsize() == 5000

    def test_get_results_empty(self) -> None:
        load_tester = tester.LoadTester()
        assert load_tester.get_results() == []

    def test_run_with_invalid_tool_args_uses_empty_dict(self, monkeypatch: Any) -> None:
        fake_sess = _FakeSession(response={"result": {"content": [{"text": "ok"}]}})
        monkeypatch.setattr(tester, "_make_session", lambda *args, **kwargs: fake_sess)

        load_tester = tester.LoadTester()
        load_tester.run(
            {
                "operation": "call_tool",
                "tool_name": "echo",
                "tool_args": "{invalid",
                "total_calls": 1,
                "concurrency": 1,
                "base_url": "https://example.com",
                "iap_token": "token",
            }
        )

        assert fake_sess.called_tool_args == {}

    def test_run_list_tools_error_response_marks_failure(
        self, monkeypatch: Any
    ) -> None:
        fake_sess = _FakeSession(response={"error": {"message": "bad request"}})
        monkeypatch.setattr(tester, "_make_session", lambda *args, **kwargs: fake_sess)

        load_tester = tester.LoadTester()
        load_tester.run(
            {
                "operation": "list_tools",
                "total_calls": 1,
                "concurrency": 1,
                "base_url": "https://example.com",
                "iap_token": "token",
            }
        )

        result = load_tester.get_results()[0]
        assert result["success"] is False

    def test_run_call_tool_is_error_with_empty_content_uses_default(
        self, monkeypatch: Any
    ) -> None:
        fake_sess = _FakeSession(response={"result": {"isError": True, "content": []}})
        monkeypatch.setattr(tester, "_make_session", lambda *args, **kwargs: fake_sess)

        load_tester = tester.LoadTester()
        load_tester.run(
            {
                "operation": "call_tool",
                "tool_name": "broken",
                "tool_args": {},
                "total_calls": 1,
                "concurrency": 1,
                "base_url": "https://example.com",
                "iap_token": "token",
            }
        )

        result = load_tester.get_results()[0]
        assert "Tool returned error" in (result["error"] or "")

    def test_run_call_tool_non_dict_result_uses_string_summary(
        self, monkeypatch: Any
    ) -> None:
        fake_sess = _FakeSession(response={"result": "plain text"})
        monkeypatch.setattr(tester, "_make_session", lambda *args, **kwargs: fake_sess)

        load_tester = tester.LoadTester()
        load_tester.run(
            {
                "operation": "call_tool",
                "tool_name": "stringy",
                "tool_args": {},
                "total_calls": 1,
                "concurrency": 1,
                "base_url": "https://example.com",
                "iap_token": "token",
            }
        )

        result = load_tester.get_results()[0]
        assert result["response_summary"] == "plain text"

    def test_run_respects_interval_sleep(self, monkeypatch: Any) -> None:
        fake_sess = _FakeSession(response={"result": {"tools": []}})
        sleeps: list[float] = []
        monkeypatch.setattr(tester, "_make_session", lambda *args, **kwargs: fake_sess)
        monkeypatch.setattr(
            "mcp_load_tester.tester.time.sleep", lambda s: sleeps.append(s)
        )

        load_tester = tester.LoadTester()
        load_tester.run(
            {
                "operation": "list_tools",
                "total_calls": 1,
                "concurrency": 1,
                "interval_ms": 10,
                "base_url": "https://example.com",
                "iap_token": "token",
            }
        )

        assert sleeps == [0.01]

    def test_run_stops_when_duration_exceeded(self, monkeypatch: Any) -> None:
        fake_sess = _FakeSession(response={"result": {"tools": []}})
        now = [1000.0, 1001.0, 1001.0, 1001.0]
        monkeypatch.setattr(tester, "_make_session", lambda *args, **kwargs: fake_sess)
        monkeypatch.setattr("mcp_load_tester.tester.time.time", lambda: now.pop(0))

        load_tester = tester.LoadTester()
        load_tester.run(
            {
                "operation": "list_tools",
                "total_calls": 5,
                "concurrency": 1,
                "duration_seconds": 0.5,
                "base_url": "https://example.com",
                "iap_token": "token",
            }
        )

        assert load_tester.get_results() == []


def _make_handler(path: str = "/") -> Any:
    handler: Any = tester._Handler.__new__(tester._Handler)
    handler.path = path
    handler.headers = {"Content-Length": "0"}
    handler.rfile = io.BytesIO(b"")
    handler.wfile = io.BytesIO()
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    handler.send_error = MagicMock()
    handler._stream = MagicMock()
    handler._run = MagicMock()
    handler._fetch_tools = MagicMock()
    handler._json = MagicMock()
    return handler


class TestHandler:
    def test_log_message_is_noop(self) -> None:
        handler = _make_handler("/")
        assert handler.log_message("%s", "x") is None

    def test_options_sets_cors_headers(self) -> None:
        handler = _make_handler("/")
        handler.do_OPTIONS()

        handler.send_response.assert_called_once_with(200)

    def test_read_json_empty_content(self) -> None:
        handler = _make_handler()

        assert handler._read_json() == {}

    def test_read_json_with_payload(self) -> None:
        handler = _make_handler()
        handler.headers = {"Content-Length": "13"}
        handler.rfile = io.BytesIO(b'{"ok": true}')

        assert handler._read_json() == {"ok": True}

    def test_json_writes_status_headers_and_body(self) -> None:
        handler = _make_handler()
        handler._json = tester._Handler._json.__get__(handler, tester._Handler)
        handler._cors_headers = MagicMock()

        handler._json({"ok": True}, status=201)

        handler.send_response.assert_called_once_with(201)
        assert b'{"ok": true}' == handler.wfile.getvalue()

    def test_do_get_root_returns_html(self) -> None:
        handler = _make_handler("/")
        handler._json = tester._Handler._json.__get__(handler, tester._Handler)

        handler.do_GET()

        handler.send_response.assert_called_once_with(200)
        assert b"Matik MCP Load Tester" in handler.wfile.getvalue()

    def test_do_get_results_uses_json_response(self, monkeypatch: Any) -> None:
        handler = _make_handler("/api/results")
        fake_tester = MagicMock()
        fake_tester.get_results.return_value = [{"ok": True}]
        monkeypatch.setattr(tester, "_tester", fake_tester)

        handler.do_GET()

        handler._json.assert_called_once_with({"results": [{"ok": True}]})

    def test_do_get_stream_routes_to_stream(self) -> None:
        handler = _make_handler("/api/stream")

        handler.do_GET()

        handler._stream.assert_called_once()

    def test_do_get_unknown_returns_404(self) -> None:
        handler = _make_handler("/unknown")

        handler.do_GET()

        handler.send_error.assert_called_once_with(404)

    def test_do_post_routes_stop(self, monkeypatch: Any) -> None:
        handler = _make_handler("/api/stop")
        fake_tester = MagicMock()
        monkeypatch.setattr(tester, "_tester", fake_tester)

        handler.do_POST()

        fake_tester.stop.assert_called_once()
        handler._json.assert_called_once_with({"status": "stopped"})

    def test_do_post_routes_run(self) -> None:
        handler = _make_handler("/api/run")

        handler.do_POST()

        handler._run.assert_called_once()

    def test_do_post_routes_fetch_tools(self) -> None:
        handler = _make_handler("/api/fetch-tools")

        handler.do_POST()

        handler._fetch_tools.assert_called_once()

    def test_do_post_unknown_returns_404(self) -> None:
        handler = _make_handler("/api/nope")

        handler.do_POST()

        handler.send_error.assert_called_once_with(404)

    def test_stream_writes_done_event_and_unsubscribes(self, monkeypatch: Any) -> None:
        handler = _make_handler("/api/stream")
        handler._stream = tester._Handler._stream.__get__(handler, tester._Handler)
        fake_tester = MagicMock()
        q: queue.Queue[dict[str, Any]] = queue.Queue()
        q.put({"type": "done", "completed": 1, "total": 1})
        fake_tester.subscribe.return_value = q
        monkeypatch.setattr(tester, "_tester", fake_tester)

        handler._stream()

        assert b"data:" in handler.wfile.getvalue()
        fake_tester.unsubscribe.assert_called_once_with(q)

    def test_stream_sends_keepalive_on_empty_queue(self, monkeypatch: Any) -> None:
        handler = _make_handler("/api/stream")
        handler._stream = tester._Handler._stream.__get__(handler, tester._Handler)

        class _QueueWithKeepalive:
            def __init__(self) -> None:
                self.calls = 0

            def get(self, timeout: float) -> dict[str, Any]:
                _ = timeout
                self.calls += 1
                if self.calls == 1:
                    raise queue.Empty()
                return {"type": "done", "completed": 1, "total": 1}

        fake_q = _QueueWithKeepalive()
        fake_tester = MagicMock()
        fake_tester.subscribe.return_value = fake_q
        monkeypatch.setattr(tester, "_tester", fake_tester)

        handler._stream()

        body = handler.wfile.getvalue()
        assert b": ka\n\n" in body
        assert b"data:" in body

    def test_stream_breaks_when_keepalive_write_fails(self, monkeypatch: Any) -> None:
        handler = _make_handler("/api/stream")
        handler._stream = tester._Handler._stream.__get__(handler, tester._Handler)

        class _QueueAlwaysEmpty:
            def get(self, timeout: float) -> dict[str, Any]:
                _ = timeout
                raise queue.Empty()

        class _FailingWriter:
            def write(self, data: bytes) -> int:
                _ = data
                raise RuntimeError("write failed")

            def flush(self) -> None:
                return None

        fake_tester = MagicMock()
        fake_tester.subscribe.return_value = _QueueAlwaysEmpty()
        monkeypatch.setattr(tester, "_tester", fake_tester)
        handler.wfile = _FailingWriter()

        handler._stream()

        fake_tester.unsubscribe.assert_called_once()

    def test_run_rejects_when_tester_already_running(self, monkeypatch: Any) -> None:
        handler = _make_handler("/api/run")
        handler._run = tester._Handler._run.__get__(handler, tester._Handler)
        handler._read_json = MagicMock(return_value={})
        handler._json = MagicMock()
        fake_tester = MagicMock()
        fake_tester.is_running.return_value = True
        monkeypatch.setattr(tester, "_tester", fake_tester)

        handler._run()

        handler._json.assert_called_once_with(
            {"error": "A test is already running. Stop it first."}, 400
        )

    def test_run_starts_thread_when_idle(self, monkeypatch: Any) -> None:
        handler = _make_handler("/api/run")
        handler._run = tester._Handler._run.__get__(handler, tester._Handler)
        handler._read_json = MagicMock(return_value={"total_calls": 1})
        handler._json = MagicMock()

        fake_tester = MagicMock()
        fake_tester.is_running.return_value = False
        monkeypatch.setattr(tester, "_tester", fake_tester)
        monkeypatch.setattr("mcp_load_tester.tester.threading.Thread", _ImmediateThread)

        handler._run()

        fake_tester.run.assert_called_once()
        handler._json.assert_called_once_with({"status": "started"})

    def test_fetch_tools_success(self, monkeypatch: Any) -> None:
        handler = _make_handler("/api/fetch-tools")
        handler._fetch_tools = tester._Handler._fetch_tools.__get__(
            handler, tester._Handler
        )
        handler._read_json = MagicMock(
            return_value={
                "transport": "sse",
                "base_url": "https://example.com",
                "iap_token": "token",
                "mcp_path": "/mcp",
                "timeout_seconds": 30,
            }
        )
        handler._json = MagicMock()
        fake_sess = _FakeSession(
            response={
                "result": {
                    "tools": [
                        {
                            "name": "a",
                            "description": "Tool A",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "incident_id": {"type": "string"},
                                    "limit": {"type": "integer", "default": 10},
                                },
                                "required": ["incident_id"],
                            },
                        }
                    ]
                }
            }
        )
        monkeypatch.setattr(tester, "_make_session", lambda *args, **kwargs: fake_sess)

        handler._fetch_tools()

        handler._json.assert_called_once()
        payload = handler._json.call_args[0][0]
        assert list(payload.keys()) == ["tools"]
        assert len(payload["tools"]) == 1
        tool = payload["tools"][0]
        assert tool["name"] == "a"
        assert tool["description"] == "Tool A"
        assert tool["required"] == ["incident_id"]
        # Preset is prefilled from the schema: default honored, type placeholder
        # used otherwise.
        assert tool["preset"] == {"incident_id": "", "limit": 10}

    def test_fetch_tools_error(self, monkeypatch: Any) -> None:
        handler = _make_handler("/api/fetch-tools")
        handler._fetch_tools = tester._Handler._fetch_tools.__get__(
            handler, tester._Handler
        )
        handler._read_json = MagicMock(return_value={})
        handler._json = MagicMock()

        def _raise(*args: Any, **kwargs: Any) -> Any:
            _ = (args, kwargs)
            raise RuntimeError("session create failed")

        monkeypatch.setattr(tester, "_make_session", _raise)

        handler._fetch_tools()

        handler._json.assert_called_once()
        assert handler._json.call_args[0][1] == 500


class TestBuildArgPreset:
    def test_empty_schema_returns_empty_dict(self) -> None:
        assert tester.build_arg_preset({}) == {}

    def test_non_dict_returns_empty_dict(self) -> None:
        assert tester.build_arg_preset(None) == {}  # type: ignore[arg-type]

    def test_no_properties_returns_empty_dict(self) -> None:
        assert tester.build_arg_preset({"type": "object"}) == {}

    def test_type_placeholders(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer"},
                "ratio": {"type": "number"},
                "active": {"type": "boolean"},
                "tags": {"type": "array"},
                "meta": {"type": "object"},
            },
        }
        assert tester.build_arg_preset(schema) == {
            "name": "",
            "count": 0,
            "ratio": 0,
            "active": False,
            "tags": [],
            "meta": {},
        }

    def test_default_takes_precedence(self) -> None:
        schema = {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 25}},
        }
        assert tester.build_arg_preset(schema) == {"limit": 25}

    def test_enum_first_value_used_when_no_default(self) -> None:
        schema = {
            "type": "object",
            "properties": {"status": {"type": "string", "enum": ["open", "closed"]}},
        }
        assert tester.build_arg_preset(schema) == {"status": "open"}

    def test_nullable_type_list_picks_non_null(self) -> None:
        schema = {
            "type": "object",
            "properties": {"note": {"type": ["string", "null"]}},
        }
        assert tester.build_arg_preset(schema) == {"note": ""}

    def test_array_with_typed_items_gets_one_sample(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "reference_ids": {"type": "array", "items": {"type": "string"}}
            },
        }
        assert tester.build_arg_preset(schema) == {"reference_ids": [""]}

    def test_nested_object_recurses(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "object",
                    "properties": {
                        "service": {"type": "string"},
                        "limit": {"type": "integer", "default": 5},
                    },
                }
            },
        }
        assert tester.build_arg_preset(schema) == {
            "filter": {"service": "", "limit": 5}
        }

    def test_required_and_optional_both_included(self) -> None:
        # The preset shows the full shape; required-ness is surfaced separately
        # (via the schema's "required" list) rather than by pruning the preset.
        schema = {
            "type": "object",
            "properties": {
                "incident_id": {"type": "string"},
                "verbose": {"type": "boolean"},
            },
            "required": ["incident_id"],
        }
        assert tester.build_arg_preset(schema) == {
            "incident_id": "",
            "verbose": False,
        }


class TestEntryPoint:
    def test_free_port_returns_positive_int(self) -> None:
        assert tester._free_port() > 0
