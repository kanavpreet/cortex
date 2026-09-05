"""Unit tests for the shared, category-neutral eval plumbing in evals/_common.py."""

import pytest

from common.clients.bedrock_client import BedrockClientSync
from common.clients.facade_client import FacadeClientSync
from evals import _common

_FAKE_KUBE_GEN = {
    "common": {
        "all": {
            "params": {
                "facade": {
                    "default_model": "matik-sandbox-gpt-5",
                    "resource_bucket": "production",
                },
                "enricher": {
                    "source_mappings": {
                        "incidentio_description_prompt": "  You summarize incidents.\n  Keep it short.\n",
                    }
                },
            }
        },
        "production": {
            "params": {
                "facade": {
                    "default_model": "matik-production-gpt-5",
                },
            }
        },
    }
}


@pytest.fixture
def fake_kube_gen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_common, "load_kube_gen", lambda: _FAKE_KUBE_GEN)


class TestLoadKubeGen:
    """Test reading the real kube-gen.yml (no mocking)."""

    def test_loads_real_file(self) -> None:
        data = _common.load_kube_gen()
        assert "common" in data


class TestResolveParam:
    """Tests for resolve_param production-first / common.all fallback."""

    def test_prefers_production_override(self, fake_kube_gen: None) -> None:
        assert (
            _common.resolve_param("facade", "default_model") == "matik-production-gpt-5"
        )

    def test_falls_back_to_common_all(self, fake_kube_gen: None) -> None:
        # resource_bucket is only defined under common.all.
        assert _common.resolve_param("facade", "resource_bucket") == "production"

    def test_missing_path_raises_keyerror(self, fake_kube_gen: None) -> None:
        with pytest.raises(KeyError):
            _common.resolve_param("facade", "nonexistent_field")


class TestLoadFacadeConfig:
    """Tests for load_facade_config (model/bucket from kube-gen, endpoint from env)."""

    def test_uses_production_model_and_bucket(
        self, fake_kube_gen: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FACADE_BASE_URL", raising=False)
        monkeypatch.delenv("FACADE_MOCK_MODE", raising=False)
        config = _common.load_facade_config()
        assert config.default_model == "matik-production-gpt-5"
        assert config.resource_bucket == "production"
        assert config.base_url == _common._DEFAULT_FACADE_BASE_URL

    def test_base_url_from_env_overrides_default(
        self, fake_kube_gen: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FACADE_BASE_URL", "http://internal:11000/x")
        config = _common.load_facade_config()
        assert config.base_url == "http://internal:11000/x"

    def test_mock_mode_parsed_from_env(
        self, fake_kube_gen: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FACADE_MOCK_MODE", "true")
        assert _common.load_facade_config().mock_mode is True
        monkeypatch.setenv("FACADE_MOCK_MODE", "false")
        assert _common.load_facade_config().mock_mode is False


class TestBuildJudgeClient:
    """Tests for build_judge_client provider/model resolution."""

    def test_bedrock_default_model(self, fake_kube_gen: None) -> None:
        client, scorer_model = _common.build_judge_client("bedrock", None)
        assert isinstance(client, BedrockClientSync)
        assert scorer_model == "global.anthropic.claude-opus-4-8"

    def test_bedrock_alias_resolves_to_full_id(self, fake_kube_gen: None) -> None:
        _, scorer_model = _common.build_judge_client("bedrock", "claude-sonnet-4-6")
        assert scorer_model == "global.anthropic.claude-sonnet-4-6"

    def test_bedrock_full_id_passes_through(self, fake_kube_gen: None) -> None:
        _, scorer_model = _common.build_judge_client(
            "bedrock", "global.meta.llama3-3-70b-instruct-v1:0"
        )
        assert scorer_model == "global.meta.llama3-3-70b-instruct-v1:0"

    def test_facade_provider_returns_facade_client(self, fake_kube_gen: None) -> None:
        client, scorer_model = _common.build_judge_client("facade", None)
        assert isinstance(client, FacadeClientSync)
        assert scorer_model is None

    def test_unknown_provider_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown judge provider"):
            _common.build_judge_client("gemini", None)


class TestMakeExperimentName:
    """Tests for make_experiment_name."""

    def test_no_suffix(self) -> None:
        name = _common.make_experiment_name("summary", "my-eval", None)
        assert name.startswith("summary-my-eval-")
        assert not name.endswith("-")

    def test_appends_suffix(self) -> None:
        name = _common.make_experiment_name("summary", "my-eval", "baseline")
        assert name.startswith("summary-my-eval-")
        assert name.endswith("-baseline")


class TestBuildTagsAndMetadata:
    """Tests for the tag/metadata builders."""

    def test_tags_are_key_value(self) -> None:
        tags = _common.build_tags(
            category="summary",
            eval_slug="my-eval",
            git_branch="evals",
            generator_model="gpt-5",
            scorer_model="opus",
        )
        assert "category:summary" in tags
        assert "eval_slug:my-eval" in tags
        assert "git_branch:evals" in tags
        assert "model:gpt-5" in tags
        assert "scorer:opus" in tags

    def test_metadata_includes_extra(self) -> None:
        metadata = _common.build_metadata(
            category="summary",
            generator_model="gpt-5",
            judge_provider="bedrock",
            scorer_model="opus",
            extra={"prompt": "the prompt"},
        )
        assert metadata["category"] == "summary"
        assert metadata["scorer_model"] == "opus"
        assert metadata["prompt"] == "the prompt"
