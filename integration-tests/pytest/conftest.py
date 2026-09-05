"""Root conftest: CLI options, Pokey env var/secrets support, and shared fixtures."""

import logging
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from config.environments import get_environment_config
from config.settings import Settings

logger = logging.getLogger(__name__)

_LOCAL_CONFIG_PATH = Path(__file__).parent / "config" / "local.yml"

# Pokey mounts any file under _infra/kube/files/ whose name starts with
# "integration-tests" into the job container under /pokey/files/ (see
# https://developers.a.musta.ch/docs/default/component/pokey/secrets-and-file-mounts/).
# _infra/kube/files/integration-tests-secrets.yml renders api.service_secret
# from secret-lair, so this is where Pokey runs pick up MATIK_API_SERVICE_SECRET
# from — there's no env var equivalent under Pokey, only this file mount.
_POKEY_SECRETS_PATH = Path("/pokey/files/integration-tests-secrets.yml")


def _load_local_config() -> dict[str, Any]:
    """Load config/local.yml if it exists, otherwise return empty dict."""
    if not _LOCAL_CONFIG_PATH.exists():
        return {}
    with open(_LOCAL_CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


def _load_pokey_secrets() -> dict[str, Any]:
    """Load the Pokey-mounted secrets file if present, otherwise return empty dict.

    Absent for local runs (no Pokey mount) and safe to treat the same as
    "no secrets" if the file is ever empty or missing a key.
    """
    if not _POKEY_SECRETS_PATH.exists():
        return {}
    with open(_POKEY_SECRETS_PATH) as f:
        return yaml.safe_load(f) or {}


def _pokey_url(service: str, port: int = 8080) -> str | None:
    """
    Build a base URL from Pokey-injected SERVICE_HOSTNAME_* env vars.

    Pokey injects SERVICE_HOSTNAME_<service> and SERVICE_PORT_<service>
    where service name has hyphens replaced by underscores (lowercase).
    Returns None if the env var is not set.
    """
    env_key = service.replace("-", "_")
    var_name = f"SERVICE_HOSTNAME_{env_key}"
    hostname = os.environ.get(var_name)
    logger.debug("Pokey lookup: %s=%r", var_name, hostname)
    if not hostname:
        return None
    resolved_port = os.environ.get(f"SERVICE_PORT_{env_key}", str(port))
    url = f"http://{hostname}:{resolved_port}"
    logger.debug("Pokey URL resolved: %s -> %s", service, url)
    return url


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register UAT-specific CLI options."""
    parser.addoption(
        "--environment",
        action="store",
        default="staging",
        choices=["sandbox", "staging", "canary", "production"],
        help="Target deployment environment (default: staging)",
    )
    parser.addoption(
        "--cell",
        action="store",
        default=None,
        help="Target cell within the environment (e.g. pug, taz, yun)",
    )


@pytest.fixture(scope="session")
def environment(request: pytest.FixtureRequest) -> str:
    """
    Resolved environment name.

    Resolution order:
    1. ENVIRONMENT env var — injected by Pokey from the Spinnaker pipeline parameter
    2. --environment CLI flag — for local runs
    """
    pokey_env = os.environ.get("ENVIRONMENT")
    if pokey_env:
        return pokey_env
    return str(request.config.getoption("--environment"))


@pytest.fixture(scope="session")
def cell(request: pytest.FixtureRequest) -> str | None:
    """Resolved cell from --cell CLI option, or None."""
    value = request.config.getoption("--cell")
    return str(value) if value else None


@pytest.fixture(scope="session")
def settings(environment: str, cell: str | None) -> Settings:
    """
    Resolved Settings for the current environment.

    Resolution order (first wins):
    1. Pokey env vars (SERVICE_HOSTNAME_matik_api etc.) — set when running under Pokey CD
    2. config/local.yml explicit overrides — uncommon, for custom endpoints only
    3. devAccess URLs from environment registry — default for local runs, driven by --environment

    In practice:
    - Pokey: SERVICE_HOSTNAME_* provides service URLs; ENVIRONMENT sets the environment
    - Local: --environment=staging → automatically uses https://api-matik-staging.a.musta.ch

    See config/local.yml.example for the local dev template (only auth headers required).
    """
    env_config = get_environment_config(environment)
    local = _load_local_config()
    pokey_secrets = _load_pokey_secrets()

    # Dump all SERVICE_HOSTNAME_* env vars so we can see exactly what Pokey injected.
    pokey_vars = {k: v for k, v in os.environ.items() if k.startswith("SERVICE_HOSTNAME_")}
    logger.debug("ENVIRONMENT env var: %r", os.environ.get("ENVIRONMENT"))
    logger.debug("Pokey SERVICE_HOSTNAME_* vars present: %s", pokey_vars or "(none)")
    logger.debug("local.yml overrides: %s", local or "(none)")
    # Log presence only, never the value — pokey_secrets holds the raw service_secret.
    logger.debug(
        "Pokey secrets file present: %s (keys: %s)",
        _POKEY_SECRETS_PATH.exists(),
        sorted(pokey_secrets.keys()) or "(none)",
    )


    # Pokey env vars take highest precedence for service URLs.
    # Pokey derives the env var key from the k8s service name (before the dot),
    # with hyphens replaced by underscores. Service names are environment-suffixed
    # (e.g. matik-api-sandbox), so we must include the environment suffix here.
    pokey_api = _pokey_url(f"matik_api_{environment}")
    pokey_mcp = _pokey_url(f"matik_mcp_{environment}")

    # When running under Pokey (inside the cluster), use cluster-internal LLM URLs.
    # When running locally, use devAccess URLs (driven by --environment).
    is_pokey = pokey_api is not None
    logger.debug("Running under Pokey: %s", is_pokey)

    default_facade = env_config.facade_base_url if is_pokey else env_config.devaccess_facade_base_url
    default_bedrock = env_config.bedrock_base_url if is_pokey else env_config.devaccess_bedrock_base_url

    resolved = Settings(
        environment=environment,
        cell=cell,
        namespace=env_config.namespace,
        api_base_url=pokey_api or local.get("api_base_url", env_config.devaccess_api_base_url),
        mcp_base_url=pokey_mcp or local.get("mcp_base_url", env_config.devaccess_mcp_base_url),
        facade_base_url=local.get("facade_base_url", default_facade),
        bedrock_base_url=local.get("bedrock_base_url", default_bedrock),
        facade_model=local.get("facade_model", env_config.facade_model),
        kubectl_context=local.get("kubectl_context", env_config.kubectl_context),
        headers=local.get("headers", {}),
        # Resolution order: MATIK_API_SERVICE_SECRET env var (manual override,
        # e.g. a local run against an enforcing environment) > the Pokey-mounted
        # secrets file (the actual path under Pokey CD — Pokey has no env var
        # equivalent, only file mounts) > local.yml (local dev fallback).
        service_secret=os.environ.get("MATIK_API_SERVICE_SECRET")
        or pokey_secrets.get("service_secret")
        or local.get("service_secret"),
    )
    logger.debug(
        "Resolved settings: api=%s mcp=%s facade=%s bedrock=%s",
        resolved.api_base_url,
        resolved.mcp_base_url,
        resolved.facade_base_url,
        resolved.bedrock_base_url,
    )
    return resolved
