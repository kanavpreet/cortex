"""Unit tests for grafana_dashboard_downloader.downloader."""

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from . import downloader


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        json_data: Any = None,
        headers: dict[str, str] | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.headers = headers or {}
        self.text = text

    @property
    def is_redirect(self) -> bool:
        return self.status_code in (301, 302, 303, 307, 308)

    def json(self) -> Any:
        return self._json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code}",
                request=MagicMock(),
                response=self,  # type: ignore[arg-type]
            )


class TestParseRef:
    def test_extracts_dashboard_uid_and_base_url_from_full_link(self) -> None:
        base_url, kind, uid = downloader.parse_ref(
            "https://grafana.a.musta.ch/d/matik-chronicler/matik-chronicler-dashboard?orgId=1",
            "token",
        )

        assert base_url == "https://grafana.a.musta.ch"
        assert kind == "dashboard"
        assert uid == "matik-chronicler"

    def test_extracts_dashboard_uid_without_slug(self) -> None:
        base_url, kind, uid = downloader.parse_ref(
            "https://grafana.a.musta.ch/d/matik-scribe?orgId=1", "token"
        )

        assert base_url == "https://grafana.a.musta.ch"
        assert kind == "dashboard"
        assert uid == "matik-scribe"

    def test_extracts_folder_uid(self) -> None:
        base_url, kind, uid = downloader.parse_ref(
            "https://grafana.a.musta.ch/dashboards/f/matik-folder/matik?orgId=1",
            "token",
        )

        assert base_url == "https://grafana.a.musta.ch"
        assert kind == "folder"
        assert uid == "matik-folder"

    def test_raises_on_url_missing_scheme(self) -> None:
        with pytest.raises(ValueError, match="Not a full URL"):
            downloader.parse_ref("grafana.a.musta.ch/d/matik-scribe", "token")

    def test_raises_when_path_not_recognised(self) -> None:
        with pytest.raises(ValueError, match="Could not recognise URL path"):
            downloader.parse_ref("https://grafana.a.musta.ch/explore", "token")

    def test_resolves_goto_short_link_to_dashboard(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        redirect_response = _FakeResponse(
            status_code=302,
            headers={"location": "/d/matik-chronicler/matik-chronicler-dashboard"},
        )
        fake_client = MagicMock()
        fake_client.__enter__.return_value.get.return_value = redirect_response
        monkeypatch.setattr(httpx, "Client", MagicMock(return_value=fake_client))

        base_url, kind, uid = downloader.parse_ref(
            "https://grafana.a.musta.ch/goto/bfmh4d833xd6oa?orgId=1", "token"
        )

        assert base_url == "https://grafana.a.musta.ch"
        assert kind == "dashboard"
        assert uid == "matik-chronicler"

    def test_raises_when_goto_link_does_not_redirect(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        non_redirect_response = _FakeResponse(status_code=404)
        fake_client = MagicMock()
        fake_client.__enter__.return_value.get.return_value = non_redirect_response
        monkeypatch.setattr(httpx, "Client", MagicMock(return_value=fake_client))

        with pytest.raises(ValueError, match="Could not resolve short link"):
            downloader.parse_ref("https://grafana.a.musta.ch/goto/bad?orgId=1", "token")


class TestFetchDashboard:
    def test_returns_dashboard_object_from_response(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        response = _FakeResponse(
            json_data={"dashboard": {"uid": "matik-scribe", "title": "Scribe"}},
        )
        monkeypatch.setattr(httpx, "get", MagicMock(return_value=response))

        dashboard = downloader.fetch_dashboard(
            "https://grafana.a.musta.ch", "matik-scribe", "token"
        )

        assert dashboard == {"uid": "matik-scribe", "title": "Scribe"}

    def test_raises_when_dashboard_key_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        response = _FakeResponse(json_data={"meta": {}})
        monkeypatch.setattr(httpx, "get", MagicMock(return_value=response))

        with pytest.raises(ValueError, match="had no 'dashboard' object"):
            downloader.fetch_dashboard(
                "https://grafana.a.musta.ch", "matik-scribe", "token"
            )

    def test_raises_http_status_error_on_4xx(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        response = _FakeResponse(status_code=404, text="not found")
        monkeypatch.setattr(httpx, "get", MagicMock(return_value=response))

        with pytest.raises(httpx.HTTPStatusError):
            downloader.fetch_dashboard(
                "https://grafana.a.musta.ch", "missing-uid", "token"
            )


class TestListFolderContents:
    def test_splits_dashboards_and_subfolders(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        response = _FakeResponse(
            json_data=[
                {"uid": "dash-1", "title": "Dash 1", "type": "dash-db"},
                {"uid": "dash-2", "title": "Dash 2", "type": "dash-db"},
                {"uid": "sub-1", "title": "Sub 1", "type": "dash-folder"},
            ]
        )
        monkeypatch.setattr(httpx, "get", MagicMock(return_value=response))

        dashboards, subfolders = downloader.list_folder_contents(
            "https://grafana.a.musta.ch", "matik-folder", "token"
        )

        assert [d["uid"] for d in dashboards] == ["dash-1", "dash-2"]
        assert [f["uid"] for f in subfolders] == ["sub-1"]

    def test_raises_http_status_error_on_4xx(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        response = _FakeResponse(status_code=403, text="forbidden")
        monkeypatch.setattr(httpx, "get", MagicMock(return_value=response))

        with pytest.raises(httpx.HTTPStatusError):
            downloader.list_folder_contents(
                "https://grafana.a.musta.ch", "matik-folder", "token"
            )


class TestSaveDashboard:
    def test_writes_sorted_pretty_json_with_trailing_newline(
        self, tmp_path: Path
    ) -> None:
        dashboard = {"title": "Scribe", "uid": "matik-scribe", "version": 1}

        path = downloader.save_dashboard(dashboard, tmp_path, "matik-scribe")

        assert path == tmp_path / "matik-scribe.json"
        content = path.read_text()
        assert content.endswith("}\n")
        assert content == json.dumps(dashboard, indent=2, sort_keys=True) + "\n"

    def test_creates_output_dir_if_missing(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b"

        downloader.save_dashboard({"uid": "x"}, nested, "x")

        assert (nested / "x.json").exists()


class TestResolveToken:
    def test_explicit_token_takes_precedence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(downloader.IAP_TOKEN_ENV_VAR, "env-token")
        monkeypatch.setattr(
            downloader, "_fetch_iap_token", MagicMock(side_effect=AssertionError)
        )

        token = downloader.resolve_token("explicit-token", "https://grafana.a.musta.ch")

        assert token == "explicit-token"

    def test_env_var_takes_precedence_over_iap_auth(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(downloader.IAP_TOKEN_ENV_VAR, "env-token")
        monkeypatch.setattr(
            downloader, "_fetch_iap_token", MagicMock(side_effect=AssertionError)
        )

        token = downloader.resolve_token(None, "https://grafana.a.musta.ch")

        assert token == "env-token"

    def test_falls_back_to_iap_auth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(downloader.IAP_TOKEN_ENV_VAR, raising=False)
        monkeypatch.setattr(
            downloader, "_fetch_iap_token", MagicMock(return_value="fresh-token")
        )

        token = downloader.resolve_token(None, "https://grafana.a.musta.ch")

        assert token == "fresh-token"


class TestFetchIapToken:
    def test_returns_stripped_stdout_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_result = MagicMock(returncode=0, stdout="a-token\n", stderr="")
        monkeypatch.setattr(subprocess, "run", MagicMock(return_value=fake_result))

        token = downloader._fetch_iap_token("https://grafana.a.musta.ch")

        assert token == "a-token"

    def test_raises_when_iap_auth_not_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(subprocess, "run", MagicMock(side_effect=FileNotFoundError))

        with pytest.raises(RuntimeError, match="airtool install iap-auth"):
            downloader._fetch_iap_token("https://grafana.a.musta.ch")

    def test_raises_on_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            subprocess,
            "run",
            MagicMock(
                side_effect=subprocess.TimeoutExpired(cmd="iap-auth", timeout=30)
            ),
        )

        with pytest.raises(RuntimeError, match="timed out"):
            downloader._fetch_iap_token("https://grafana.a.musta.ch")

    def test_raises_when_returncode_nonzero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_result = MagicMock(returncode=1, stdout="", stderr="not authorized")
        monkeypatch.setattr(subprocess, "run", MagicMock(return_value=fake_result))

        with pytest.raises(RuntimeError, match="not authorized"):
            downloader._fetch_iap_token("https://grafana.a.musta.ch")


class TestMain:
    def test_returns_error_when_token_resolution_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(downloader.IAP_TOKEN_ENV_VAR, raising=False)
        monkeypatch.setattr(
            downloader,
            "_fetch_iap_token",
            MagicMock(side_effect=RuntimeError("iap-auth is not installed")),
        )

        result = downloader.main(["https://grafana.a.musta.ch/d/matik-scribe"])

        assert result == 1

    def test_returns_error_on_invalid_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(downloader.IAP_TOKEN_ENV_VAR, "token")

        result = downloader.main(["https://grafana.a.musta.ch/explore"])

        assert result == 1

    def test_downloads_and_saves_single_dashboard_end_to_end(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(downloader.IAP_TOKEN_ENV_VAR, "token")
        response = _FakeResponse(
            json_data={"dashboard": {"uid": "matik-scribe", "title": "Scribe"}},
        )
        monkeypatch.setattr(httpx, "get", MagicMock(return_value=response))

        result = downloader.main(
            [
                "https://grafana.a.musta.ch/d/matik-scribe/scribe-dashboard?orgId=1",
                "--output-dir",
                str(tmp_path),
            ]
        )

        assert result == 0
        saved = tmp_path / "matik-scribe.json"
        assert saved.exists()
        assert json.loads(saved.read_text())["title"] == "Scribe"

    def test_returns_error_when_fetch_fails(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(downloader.IAP_TOKEN_ENV_VAR, "token")
        response = _FakeResponse(status_code=500, text="boom")
        monkeypatch.setattr(httpx, "get", MagicMock(return_value=response))

        result = downloader.main(
            [
                "https://grafana.a.musta.ch/d/matik-scribe",
                "--output-dir",
                str(tmp_path),
            ]
        )

        assert result == 1

    def test_downloads_all_dashboards_in_a_folder(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(downloader.IAP_TOKEN_ENV_VAR, "token")
        search_response = _FakeResponse(
            json_data=[
                {"uid": "dash-1", "title": "Dash 1", "type": "dash-db"},
                {"uid": "dash-2", "title": "Dash 2", "type": "dash-db"},
            ]
        )
        dash_responses = {
            "dash-1": _FakeResponse(
                json_data={"dashboard": {"uid": "dash-1", "title": "Dash 1"}}
            ),
            "dash-2": _FakeResponse(
                json_data={"dashboard": {"uid": "dash-2", "title": "Dash 2"}}
            ),
        }

        def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
            if url.endswith("/api/search"):
                return search_response
            for uid, resp in dash_responses.items():
                if url.endswith(f"/api/dashboards/uid/{uid}"):
                    return resp
            raise AssertionError(f"Unexpected URL: {url}")

        monkeypatch.setattr(httpx, "get", fake_get)

        result = downloader.main(
            [
                "https://grafana.a.musta.ch/dashboards/f/matik-folder/matik?orgId=1",
                "--output-dir",
                str(tmp_path),
            ]
        )

        assert result == 0
        assert (tmp_path / "dash-1.json").exists()
        assert (tmp_path / "dash-2.json").exists()

    def test_folder_download_reports_failure_if_any_dashboard_fails(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(downloader.IAP_TOKEN_ENV_VAR, "token")
        search_response = _FakeResponse(
            json_data=[
                {"uid": "dash-1", "title": "Dash 1", "type": "dash-db"},
                {"uid": "dash-2", "title": "Dash 2", "type": "dash-db"},
            ]
        )

        def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
            if url.endswith("/api/search"):
                return search_response
            if url.endswith("/api/dashboards/uid/dash-1"):
                return _FakeResponse(
                    json_data={"dashboard": {"uid": "dash-1", "title": "Dash 1"}}
                )
            if url.endswith("/api/dashboards/uid/dash-2"):
                return _FakeResponse(status_code=500, text="boom")
            raise AssertionError(f"Unexpected URL: {url}")

        monkeypatch.setattr(httpx, "get", fake_get)

        result = downloader.main(
            [
                "https://grafana.a.musta.ch/dashboards/f/matik-folder/matik?orgId=1",
                "--output-dir",
                str(tmp_path),
            ]
        )

        assert result == 1
        assert (tmp_path / "dash-1.json").exists()
        assert not (tmp_path / "dash-2.json").exists()

    def test_download_one_reports_network_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(downloader.IAP_TOKEN_ENV_VAR, "token")
        monkeypatch.setattr(
            httpx, "get", MagicMock(side_effect=httpx.ConnectError("refused"))
        )

        result = downloader.main(
            [
                "https://grafana.a.musta.ch/d/matik-scribe",
                "--output-dir",
                str(tmp_path),
            ]
        )

        assert result == 1

    def test_folder_listing_reports_network_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(downloader.IAP_TOKEN_ENV_VAR, "token")
        monkeypatch.setattr(
            httpx, "get", MagicMock(side_effect=httpx.ConnectError("refused"))
        )

        result = downloader.main(
            [
                "https://grafana.a.musta.ch/dashboards/f/matik-folder/matik?orgId=1",
                "--output-dir",
                str(tmp_path),
            ]
        )

        assert result == 1

    def test_folder_listing_reports_http_status_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(downloader.IAP_TOKEN_ENV_VAR, "token")
        monkeypatch.setattr(
            httpx, "get", MagicMock(return_value=_FakeResponse(status_code=403))
        )

        result = downloader.main(
            [
                "https://grafana.a.musta.ch/dashboards/f/matik-folder/matik?orgId=1",
                "--output-dir",
                str(tmp_path),
            ]
        )

        assert result == 1

    def test_folder_with_only_subfolders_prints_warning_and_returns_zero(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv(downloader.IAP_TOKEN_ENV_VAR, "token")
        search_response = _FakeResponse(
            json_data=[{"uid": "sub-1", "title": "Sub 1", "type": "dash-folder"}]
        )
        monkeypatch.setattr(httpx, "get", MagicMock(return_value=search_response))

        result = downloader.main(
            [
                "https://grafana.a.musta.ch/dashboards/f/matik-folder/matik?orgId=1",
                "--output-dir",
                str(tmp_path),
            ]
        )

        assert result == 0
        output = capsys.readouterr().out
        assert "Sub 1" in output
        assert "No dashboards found" in output
