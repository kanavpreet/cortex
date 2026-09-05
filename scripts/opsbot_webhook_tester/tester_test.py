"""Unit tests for opsbot_webhook_tester.tester."""

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from . import tester


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, text: str = "") -> None:
        self.status_code = status_code
        self.text = text

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300


class _FakeClient:
    """Stands in for httpx.Client as a context manager.

    Each entry in ``responses`` is either a ``_FakeResponse`` to return or an
    ``Exception`` instance to raise, so callers can simulate a per-request
    network failure (e.g. a timeout) alongside successful calls.
    """

    def __init__(self, responses: list[_FakeResponse | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def post(
        self, url: str, *, content: bytes, headers: dict[str, str]
    ) -> _FakeResponse:
        self.calls.append({"url": url, "content": content, "headers": headers})
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class TestLoadSummaries:
    def test_loads_nonempty_list_of_strings(self) -> None:
        summaries = tester.load_summaries()

        assert isinstance(summaries, list)
        assert len(summaries) > 1
        assert all(isinstance(s, str) and s for s in summaries)

    def test_loads_from_given_path(self, tmp_path: Path) -> None:
        custom = tmp_path / "summaries.json"
        custom.write_text(json.dumps(["only one"]))

        assert tester.load_summaries(custom) == ["only one"]


class TestSign:
    def test_matches_expected_hmac(self) -> None:
        body = b'{"a": 1}'
        secret = "shhh"

        result = tester.sign(body, secret)

        expected = (
            "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        )
        assert result == expected

    def test_different_secrets_produce_different_signatures(self) -> None:
        body = b"same body"

        assert tester.sign(body, "secret-a") != tester.sign(body, "secret-b")


class TestBuildPayload:
    def test_envelope_matches_opsbot_contract(self) -> None:
        payload = tester.build_payload("inc-1234", "a test summary")

        assert payload["category"] == "incident_channel_summary"
        assert payload["source"] == "opsbot"
        assert payload["entity_id"] == "inc-1234"
        assert payload["data"] == {"summary": "a test summary"}
        # generated_at must be a Zulu-suffixed timestamp, not tz-aware isoformat.
        assert payload["generated_at"].endswith("Z")


class TestPostSummary:
    def test_signs_body_and_posts_expected_payload(self) -> None:
        client = _FakeClient([_FakeResponse(status_code=200)])

        response = tester.post_summary(
            client,  # type: ignore[arg-type]
            "https://example.test/callback",
            "shhh",
            "inc-1234",
            "a test summary",
        )

        assert response.status_code == 200
        assert len(client.calls) == 1
        call = client.calls[0]
        assert call["url"] == "https://example.test/callback"
        sent_payload = json.loads(call["content"])
        assert sent_payload["entity_id"] == "inc-1234"
        assert sent_payload["data"]["summary"] == "a test summary"
        assert call["headers"][tester.SIGNATURE_HEADER] == tester.sign(
            call["content"], "shhh"
        )


class TestMain:
    def test_returns_error_when_secret_env_var_not_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(tester.GENERIC_WEBHOOK_SECRET_ENV_VAR, raising=False)
        monkeypatch.setattr(tester, "INCIDENT_REFS", ["inc-1234"])

        assert tester.main() == 1

    def test_returns_error_when_secret_env_var_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(tester.GENERIC_WEBHOOK_SECRET_ENV_VAR, "")
        monkeypatch.setattr(tester, "INCIDENT_REFS", ["inc-1234"])

        assert tester.main() == 1

    def test_posts_each_incident_and_returns_zero_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(tester.GENERIC_WEBHOOK_SECRET_ENV_VAR, "sandbox-secret")
        monkeypatch.setattr(tester, "INCIDENT_REFS", ["inc-1111", "inc-2222"])
        monkeypatch.setattr(tester, "YOYO_SANDBOX_URL", "https://x.test")
        fake_client = _FakeClient(
            [_FakeResponse(status_code=200), _FakeResponse(status_code=200)]
        )
        monkeypatch.setattr(httpx, "Client", MagicMock(return_value=fake_client))

        assert tester.main() == 0
        assert len(fake_client.calls) == 2
        assert fake_client.calls[0]["url"] == "https://x.test"
        assert fake_client.calls[0]["headers"][tester.SIGNATURE_HEADER] == tester.sign(
            fake_client.calls[0]["content"], "sandbox-secret"
        )

    def test_returns_one_when_any_post_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(tester.GENERIC_WEBHOOK_SECRET_ENV_VAR, "sandbox-secret")
        monkeypatch.setattr(tester, "INCIDENT_REFS", ["inc-1111", "inc-2222"])
        fake_client = _FakeClient(
            [
                _FakeResponse(status_code=200),
                _FakeResponse(status_code=500, text="boom"),
            ]
        )
        monkeypatch.setattr(httpx, "Client", MagicMock(return_value=fake_client))

        assert tester.main() == 1

    def test_network_error_on_one_incident_does_not_abort_the_run(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A ReadTimeout (or any httpx.HTTPError) on one POST is reported and
        skipped rather than crashing the run — the remaining incidents still
        get posted."""
        monkeypatch.setenv(tester.GENERIC_WEBHOOK_SECRET_ENV_VAR, "sandbox-secret")
        monkeypatch.setattr(tester, "INCIDENT_REFS", ["inc-1111", "inc-2222"])
        fake_client = _FakeClient(
            [httpx.ReadTimeout("timed out"), _FakeResponse(status_code=200)]
        )
        monkeypatch.setattr(httpx, "Client", MagicMock(return_value=fake_client))

        result = tester.main()

        assert result == 1
        assert len(fake_client.calls) == 2
        assert "FAILED" in capsys.readouterr().out
