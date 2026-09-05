"""API test fixtures: httpx clients for matik-api and matik-mcp."""

import pytest
import httpx

from config.settings import Settings
from helpers.api_client import build_client


@pytest.fixture(scope="session")
def api_client(settings: Settings) -> httpx.Client:
    """Session-scoped httpx Client pre-configured for matik-api."""
    with build_client(
        base_url=settings.api_base_url,
        headers=settings.headers,
        service_secret=settings.service_secret,
    ) as client:
        yield client


@pytest.fixture(scope="session")
def mcp_client(settings: Settings) -> httpx.Client:
    """Session-scoped httpx Client pre-configured for matik-mcp.

    Not signed with service_secret: that check runs on matik-api's own
    routes (Phase 1c), not on matik-mcp's, so it would be a no-op here.
    """
    with build_client(base_url=settings.mcp_base_url, headers=settings.headers) as client:
        yield client
