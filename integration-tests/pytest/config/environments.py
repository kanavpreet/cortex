"""
Environment registry for UAT.

Derived from _infra/kube/kube-gen.yml and _infra/mesh.yml.
Maps environment names to namespaces, endpoints, and kubectl contexts.
"""

from dataclasses import dataclass


# Cluster-internal base URLs (used by Pokey runner inside the cluster)
_LLM_FUSION_HUB_CLUSTER = "http://llm-fusion-hub-{env}.llm-fusion-hub-{env}:11000"
_FACADE_PATH = "/api/v2/proxy/azure/oai"
_BEDROCK_PATH = "/api/v2/proxy/aws/bedrock"

# devAccess base URL for LLM Fusion Hub (single endpoint, IAP-protected, works locally)
_LLM_FUSION_HUB_DEVACCESS = "https://llm-fusion-hub.a.musta.ch"


@dataclass
class EnvironmentConfig:
    """Configuration for a single deployment environment."""

    namespace: str
    # Cluster-internal URLs — used by Pokey (SERVICE_HOSTNAME_* overrides these)
    api_base_url: str
    mcp_base_url: str
    facade_base_url: str
    bedrock_base_url: str
    # devAccess URLs — used for local runs via AirMesh (IAP-protected)
    devaccess_api_base_url: str
    devaccess_mcp_base_url: str
    devaccess_facade_base_url: str
    devaccess_bedrock_base_url: str
    # LLM model deployment names (Azure OAI deployment names, environment-specific)
    facade_model: str
    iam_role: str
    kubectl_context: str | None = None


# Registry derived from kube-gen.yml and mesh.yml
_REGISTRY: dict[str, EnvironmentConfig] = {
    "sandbox": EnvironmentConfig(
        namespace="matik-sandbox",
        api_base_url="http://matik-api-sandbox.matik-sandbox:8080",
        mcp_base_url="http://matik-mcp-sandbox.matik-sandbox:8080",
        facade_base_url=_LLM_FUSION_HUB_CLUSTER.format(env="staging") + _FACADE_PATH,
        bedrock_base_url=_LLM_FUSION_HUB_CLUSTER.format(env="staging") + _BEDROCK_PATH,
        devaccess_api_base_url="https://api-matik-sandbox.a.musta.ch",
        devaccess_mcp_base_url="https://mcp-matik-sandbox.a.musta.ch",
        devaccess_facade_base_url=_LLM_FUSION_HUB_DEVACCESS + _FACADE_PATH,
        devaccess_bedrock_base_url=_LLM_FUSION_HUB_DEVACCESS + _BEDROCK_PATH,
        facade_model="matik-sandbox-gpt-4o",
        iam_role="matik-sandbox",
    ),
    "staging": EnvironmentConfig(
        namespace="matik-staging",
        api_base_url="http://matik-api-staging.matik-staging:8080",
        mcp_base_url="http://matik-mcp-staging.matik-staging:8080",
        facade_base_url=_LLM_FUSION_HUB_CLUSTER.format(env="staging") + _FACADE_PATH,
        bedrock_base_url=_LLM_FUSION_HUB_CLUSTER.format(env="staging") + _BEDROCK_PATH,
        devaccess_api_base_url="https://api-matik-staging.a.musta.ch",
        devaccess_mcp_base_url="https://mcp-matik-staging.a.musta.ch",
        devaccess_facade_base_url=_LLM_FUSION_HUB_DEVACCESS + _FACADE_PATH,
        devaccess_bedrock_base_url=_LLM_FUSION_HUB_DEVACCESS + _BEDROCK_PATH,
        facade_model="matik-staging-gpt-4o",
        iam_role="matik-staging",
    ),
    "canary": EnvironmentConfig(
        namespace="matik-canary",
        api_base_url="http://matik-api-canary.matik-canary:8080",
        mcp_base_url="http://matik-mcp-canary.matik-canary:8080",
        facade_base_url=_LLM_FUSION_HUB_CLUSTER.format(env="production") + _FACADE_PATH,
        bedrock_base_url=_LLM_FUSION_HUB_CLUSTER.format(env="production") + _BEDROCK_PATH,
        devaccess_api_base_url="https://api-matik-canary.a.musta.ch",
        devaccess_mcp_base_url="https://mcp-matik-canary.a.musta.ch",
        devaccess_facade_base_url=_LLM_FUSION_HUB_DEVACCESS + _FACADE_PATH,
        devaccess_bedrock_base_url=_LLM_FUSION_HUB_DEVACCESS + _BEDROCK_PATH,
        facade_model="matik-production-gpt-4o",
        iam_role="matik-production",
    ),
    "production": EnvironmentConfig(
        namespace="matik-production",
        api_base_url="http://matik-api-production.matik-production:8080",
        mcp_base_url="http://matik-mcp-production.matik-production:8080",
        facade_base_url=_LLM_FUSION_HUB_CLUSTER.format(env="production") + _FACADE_PATH,
        bedrock_base_url=_LLM_FUSION_HUB_CLUSTER.format(env="production") + _BEDROCK_PATH,
        devaccess_api_base_url="https://api-matik.a.musta.ch",
        devaccess_mcp_base_url="https://mcp-matik.a.musta.ch",
        devaccess_facade_base_url=_LLM_FUSION_HUB_DEVACCESS + _FACADE_PATH,
        devaccess_bedrock_base_url=_LLM_FUSION_HUB_DEVACCESS + _BEDROCK_PATH,
        facade_model="matik-production-gpt-4o",
        iam_role="matik-production",
    ),
}


def get_environment_config(environment: str) -> EnvironmentConfig:
    """Look up environment config by name. Raises KeyError if unknown."""
    if environment not in _REGISTRY:
        known = ", ".join(_REGISTRY.keys())
        raise KeyError(
            f"Unknown environment: {environment!r}. Known environments: {known}"
        )
    return _REGISTRY[environment]
