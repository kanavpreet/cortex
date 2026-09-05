"""LLM test fixtures: Facade and Bedrock clients from settings."""

import httpx
import pytest

from config.settings import Settings
from helpers.api_client import build_client


@pytest.fixture(scope="session")
def facade_client(settings: Settings) -> httpx.Client:
    """Session-scoped httpx Client for the LLM Fusion Hub Facade (Azure OAI proxy)."""
    with build_client(base_url=settings.facade_base_url, headers=settings.headers) as client:
        yield client


@pytest.fixture(scope="session")
def bedrock_client(settings: Settings) -> httpx.Client:
    """Session-scoped httpx Client for the LLM Fusion Hub Bedrock proxy."""
    with build_client(base_url=settings.bedrock_base_url, headers=settings.headers) as client:
        yield client


@pytest.fixture(scope="session")
def api_client(settings: Settings) -> httpx.Client:
    """Session-scoped httpx Client pre-configured for matik-api."""
    with build_client(base_url=settings.api_base_url, headers=settings.headers) as client:
        yield client
