"""Unit tests for service_signature_tester.tester."""

import hashlib
import hmac
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from . import tester


class _FakeResponse:
    def __init__(self, *, status_code: int = 200) -> None:
        self.status_code = status_code


class _FakeClient:
    """Stands in for httpx.Client as a context manager.

    Each entry in ``responses`` is either a ``_FakeResponse`` to return or an
    ``Exception`` instance to raise, mirroring a per-request network failure.
    """

    def __init__(self, responses: list[_FakeResponse | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def request(
        self,
        method: str,
        path: str,
        *,
        content: bytes | None,
        headers: dict[str, str],
    ) -> _FakeResponse:
        self.calls.append(
            {"method": method, "path": path, "content": content, "headers": headers}
        )
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class TestSign:
    """Tests for sign()."""

    def test_matches_expected_hmac(self) -> None:
        secret = "shhh"
        result = tester.sign(secret, "GET", "/v1/x", b"", "100")

        material = b".".join(
            [b"100", b"GET", b"/v1/x", hashlib.sha256(b"").hexdigest().encode()]
        )
        expected = (
            "sha256=" + hmac.new(secret.encode(), material, hashlib.sha256).hexdigest()
        )
        assert result == expected

    def test_changing_any_input_changes_signature(self) -> None:
        baseline = tester.sign("secret", "GET", "/a", b"", "100")

        assert tester.sign("secret", "POST", "/a", b"", "100") != baseline
        assert tester.sign("secret", "GET", "/b", b"", "100") != baseline
        assert tester.sign("secret", "GET", "/a", b"x", "100") != baseline
        assert tester.sign("secret", "GET", "/a", b"", "200") != baseline
        assert tester.sign("other-secret", "GET", "/a", b"", "100") != baseline

    def test_known_vector_matches_service_auth_py(self) -> None:
        """Fixed input -> fixed hex digest, kept identical to a matching test
        in matik/common/utils/service_auth_test.py.

        This script intentionally reimplements the HMAC construction from
        common/utils/service_auth.py rather than importing it (see the
        module docstring) so it has no dependency on the matik app package.
        This vector is the tripwire for that duplication: if the canonical
        construction ever changes, this test's expected digest must be
        updated by hand, which is the prompt to go check
        service_auth_test.py's matching vector too.
        """
        result = tester.sign(
            "test-shared-secret",
            "POST",
            "/v1/mcp/incidentio?foo=bar",
            b'{"a":1}',
            "1700000000",
        )
        assert (
            result
            == "sha256=0dadceac05c381038b192edf1bc99a9ca1adb29c92b4ad7b1c0e70146fc4fc1b"
        )


class TestBuildScenarios:
    """Tests for build_scenarios()."""

    def test_returns_expected_scenario_names(self) -> None:
        names = [s.name for s in tester.build_scenarios("shhh")]

        assert names == [
            "valid_signature",
            "missing_signature",
            "wrong_secret",
            "stale_timestamp",
            "malformed_timestamp",
            "tampered_body",
            "tampered_query",
            "exempt:/health",
            "exempt:/ready",
            "exempt:/v1/mcp/health",
            "exempt:/",
        ]

    def test_valid_signature_scenario_verifies(self) -> None:
        scenarios = {s.name: s for s in tester.build_scenarios("shhh")}
        valid = scenarios["valid_signature"]

        assert valid.conditional is False
        expected_sig = tester.sign(
            "shhh",
            "GET",
            tester.GET_TARGET_PATH,
            b"",
            valid.headers[tester.TIMESTAMP_HEADER],
        )
        assert valid.headers[tester.SIGNATURE_HEADER] == expected_sig

    def test_wrong_secret_signature_differs_from_valid(self) -> None:
        scenarios = {s.name: s for s in tester.build_scenarios("shhh")}

        assert (
            scenarios["wrong_secret"].headers[tester.SIGNATURE_HEADER]
            != scenarios["valid_signature"].headers[tester.SIGNATURE_HEADER]
        )

    def test_stale_timestamp_is_older_than_skew_window(self) -> None:
        scenarios = {s.name: s for s in tester.build_scenarios("shhh")}
        stale_ts = int(scenarios["stale_timestamp"].headers[tester.TIMESTAMP_HEADER])

        assert int(time.time()) - stale_ts >= tester.STALE_SKEW_SECONDS

    def test_malformed_timestamp_is_not_numeric(self) -> None:
        scenarios = {s.name: s for s in tester.build_scenarios("shhh")}

        with pytest.raises(ValueError):
            int(scenarios["malformed_timestamp"].headers[tester.TIMESTAMP_HEADER])

    def test_tampered_body_sends_different_body_than_signed(self) -> None:
        scenarios = {s.name: s for s in tester.build_scenarios("shhh")}
        tampered = scenarios["tampered_body"]

        expected_sig = tester.sign(
            "shhh",
            "POST",
            tester.POST_TARGET_PATH,
            b'{"incident_ids": ["SIGNED-FOR-THIS"]}',
            tampered.headers[tester.TIMESTAMP_HEADER],
        )
        assert tampered.headers[tester.SIGNATURE_HEADER] == expected_sig
        assert tampered.body == b'{"incident_ids": ["ACTUALLY-SENT-THIS"]}'

    def test_tampered_query_signs_bare_path_but_sends_with_query(self) -> None:
        scenarios = {s.name: s for s in tester.build_scenarios("shhh")}
        tampered = scenarios["tampered_query"]

        assert tampered.path == tester.GET_TARGET_PATH
        assert tampered.sent_path == f"{tester.GET_TARGET_PATH}?evil=1"
        expected_sig = tester.sign(
            "shhh",
            "GET",
            tester.GET_TARGET_PATH,
            b"",
            tampered.headers[tester.TIMESTAMP_HEADER],
        )
        assert tampered.headers[tester.SIGNATURE_HEADER] == expected_sig

    def test_exempt_scenarios_are_unconditional_and_unsigned(self) -> None:
        for s in tester.build_scenarios("shhh"):
            if s.name.startswith("exempt:"):
                assert s.conditional is False
                assert s.headers == {}

    def test_only_tampered_query_sets_sent_path(self) -> None:
        for s in tester.build_scenarios("shhh"):
            if s.name == "tampered_query":
                assert s.sent_path is not None
            else:
                assert s.sent_path is None


class TestRunScenario:
    """Tests for run_scenario()."""

    def test_sends_to_sent_path_when_set(self) -> None:
        client = _FakeClient([_FakeResponse(status_code=401)])
        scenario = tester.Scenario(
            name="x",
            method="GET",
            path="/a",
            sent_path="/a?evil=1",
            headers={"h": "1"},
            conditional=True,
        )

        status, matched = tester.run_scenario(client, scenario, enforced=True)  # type: ignore[arg-type]

        assert status == 401
        assert matched is True
        assert client.calls[0]["path"] == "/a?evil=1"

    def test_falls_back_to_path_when_sent_path_unset(self) -> None:
        client = _FakeClient([_FakeResponse(status_code=200)])
        scenario = tester.Scenario(
            name="x", method="GET", path="/a", headers={}, conditional=False
        )

        tester.run_scenario(client, scenario, enforced=True)  # type: ignore[arg-type]

        assert client.calls[0]["path"] == "/a"

    def test_conditional_expected_status_depends_on_enforced(self) -> None:
        client = _FakeClient(
            [_FakeResponse(status_code=200), _FakeResponse(status_code=200)]
        )
        scenario = tester.Scenario(
            name="x", method="GET", path="/a", headers={}, conditional=True
        )

        _, matched_shadow = tester.run_scenario(client, scenario, enforced=False)  # type: ignore[arg-type]
        assert matched_shadow is True  # 200 is expected in shadow mode

        _, matched_enforced = tester.run_scenario(client, scenario, enforced=True)  # type: ignore[arg-type]
        assert matched_enforced is False  # 401 expected when enforced, got 200

    def test_unconditional_scenario_always_expects_200(self) -> None:
        client = _FakeClient(
            [_FakeResponse(status_code=200), _FakeResponse(status_code=200)]
        )
        scenario = tester.Scenario(
            name="x", method="GET", path="/a", headers={}, conditional=False
        )

        _, matched_shadow = tester.run_scenario(client, scenario, enforced=False)  # type: ignore[arg-type]
        _, matched_enforced = tester.run_scenario(client, scenario, enforced=True)  # type: ignore[arg-type]

        assert matched_shadow is True
        assert matched_enforced is True

    def test_body_is_sent_as_content(self) -> None:
        client = _FakeClient([_FakeResponse(status_code=200)])
        scenario = tester.Scenario(
            name="x",
            method="POST",
            path="/a",
            headers={},
            conditional=False,
            body=b"payload",
        )

        tester.run_scenario(client, scenario, enforced=False)  # type: ignore[arg-type]

        assert client.calls[0]["content"] == b"payload"

    def test_empty_body_sent_as_none(self) -> None:
        client = _FakeClient([_FakeResponse(status_code=200)])
        scenario = tester.Scenario(
            name="x", method="GET", path="/a", headers={}, conditional=False
        )

        tester.run_scenario(client, scenario, enforced=False)  # type: ignore[arg-type]

        assert client.calls[0]["content"] is None


class TestGenerateHtmlReport:
    """Tests for generate_html_report()."""

    def test_all_ok_renders_success_summary(self) -> None:
        results = [
            {
                "name": "valid_signature",
                "method": "GET",
                "path": "/a",
                "actual": 200,
                "expected": 200,
                "note": "n",
                "outcome": "ok",
            },
        ]

        rendered = tester.generate_html_report(results, "https://api.test", "shadow", 0)

        assert "All 1 scenarios matched" in rendered
        assert "valid_signature" in rendered
        assert "https://api.test" in rendered
        assert "shadow" in rendered

    def test_mismatches_render_failure_summary_and_all_outcome_styles(self) -> None:
        results: list[dict[str, Any]] = [
            {
                "name": "a",
                "method": "GET",
                "path": "/a",
                "actual": 200,
                "expected": 200,
                "note": "n",
                "outcome": "ok",
            },
            {
                "name": "b",
                "method": "GET",
                "path": "/b",
                "actual": 200,
                "expected": 401,
                "note": "n",
                "outcome": "mismatch",
            },
            {
                "name": "c",
                "method": "GET",
                "path": "/c",
                "actual": None,
                "expected": 200,
                "note": "boom",
                "outcome": "error",
            },
        ]

        rendered = tester.generate_html_report(
            results, "https://api.test", "enforced", 2
        )

        assert "2 of 3 scenario(s) did not match" in rendered
        assert "—" in rendered  # actual=None renders as an em-dash
        assert "MISMATCH" in rendered
        assert "ERROR" in rendered

    def test_escapes_html_in_values(self) -> None:
        results = [
            {
                "name": "<script>",
                "method": "GET",
                "path": "/a",
                "actual": 200,
                "expected": 200,
                "note": "n",
                "outcome": "ok",
            },
        ]

        rendered = tester.generate_html_report(results, "https://api.test", "shadow", 0)

        assert "<script>" not in rendered
        assert "&lt;script&gt;" in rendered


class TestMain:
    """Tests for main()."""

    def test_returns_error_when_no_secret(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv(tester.SECRET_ENV_VAR, raising=False)
        monkeypatch.setattr(sys, "argv", ["prog"])

        assert tester.main() == 1
        assert "Set MATIK_API_SERVICE_SECRET" in capsys.readouterr().err

    def test_all_scenarios_pass_returns_zero_and_writes_html_report(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(tester.SECRET_ENV_VAR, "shhh")
        report_path = tmp_path / "report.html"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "prog",
                "--base-url",
                "https://api.test",
                "--html-report",
                str(report_path),
            ],
        )
        num_scenarios = len(tester.build_scenarios("shhh"))
        fake_client = _FakeClient(
            [_FakeResponse(status_code=200) for _ in range(num_scenarios)]
        )
        monkeypatch.setattr(httpx, "Client", MagicMock(return_value=fake_client))

        assert tester.main() == 0
        assert report_path.exists()
        assert "Service Signature Tester Report" in report_path.read_text()

    def test_enforced_posture_with_correct_statuses_returns_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(tester.SECRET_ENV_VAR, "shhh")
        monkeypatch.setattr(sys, "argv", ["prog", "--enforced", "--html-report", ""])
        scenarios = tester.build_scenarios("shhh")
        fake_client = _FakeClient(
            [
                _FakeResponse(status_code=401 if s.conditional else 200)
                for s in scenarios
            ]
        )
        monkeypatch.setattr(httpx, "Client", MagicMock(return_value=fake_client))

        assert tester.main() == 0

    def test_mismatch_returns_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(tester.SECRET_ENV_VAR, "shhh")
        monkeypatch.setattr(sys, "argv", ["prog", "--html-report", ""])
        num_scenarios = len(tester.build_scenarios("shhh"))
        responses: list[_FakeResponse | Exception] = [_FakeResponse(status_code=500)]
        responses += [_FakeResponse(status_code=200) for _ in range(num_scenarios - 1)]
        fake_client = _FakeClient(responses)
        monkeypatch.setattr(httpx, "Client", MagicMock(return_value=fake_client))

        assert tester.main() == 1

    def test_http_error_counts_as_mismatch_and_is_reported(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv(tester.SECRET_ENV_VAR, "shhh")
        monkeypatch.setattr(sys, "argv", ["prog", "--html-report", ""])
        num_scenarios = len(tester.build_scenarios("shhh"))
        responses: list[_FakeResponse | Exception] = [httpx.ReadTimeout("timed out")]
        responses += [_FakeResponse(status_code=200) for _ in range(num_scenarios - 1)]
        fake_client = _FakeClient(responses)
        monkeypatch.setattr(httpx, "Client", MagicMock(return_value=fake_client))

        result = tester.main()

        assert result == 1
        assert "ERROR" in capsys.readouterr().out

    def test_iap_token_sets_authorization_header(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(tester.SECRET_ENV_VAR, "shhh")
        monkeypatch.setattr(
            sys, "argv", ["prog", "--iap-token", "tok123", "--html-report", ""]
        )
        num_scenarios = len(tester.build_scenarios("shhh"))
        fake_client = _FakeClient(
            [_FakeResponse(status_code=200) for _ in range(num_scenarios)]
        )
        mock_client_cls = MagicMock(return_value=fake_client)
        monkeypatch.setattr(httpx, "Client", mock_client_cls)

        tester.main()

        _, kwargs = mock_client_cls.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer tok123"

    def test_secret_from_flag_overrides_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(tester.SECRET_ENV_VAR, raising=False)
        monkeypatch.setattr(
            sys, "argv", ["prog", "--secret", "flag-secret", "--html-report", ""]
        )
        num_scenarios = len(tester.build_scenarios("flag-secret"))
        fake_client = _FakeClient(
            [_FakeResponse(status_code=200) for _ in range(num_scenarios)]
        )
        monkeypatch.setattr(httpx, "Client", MagicMock(return_value=fake_client))

        assert tester.main() == 0

    def test_empty_html_report_flag_skips_writing(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv(tester.SECRET_ENV_VAR, "shhh")
        monkeypatch.setattr(sys, "argv", ["prog", "--html-report", ""])
        num_scenarios = len(tester.build_scenarios("shhh"))
        fake_client = _FakeClient(
            [_FakeResponse(status_code=200) for _ in range(num_scenarios)]
        )
        monkeypatch.setattr(httpx, "Client", MagicMock(return_value=fake_client))

        tester.main()

        assert "HTML report written" not in capsys.readouterr().out
