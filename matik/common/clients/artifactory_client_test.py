"""Tests for ArtifactoryClient."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from common.clients.artifactory_client import (
    ArtifactoryClient,
    create_artifactory_client,
)
from common.models.artifactory_config import ArtifactoryConfig


class TestArtifactoryClient:
    """Test ArtifactoryClient."""

    def test_init_strips_trailing_slash(self) -> None:
        """Test that base_url trailing slash is stripped."""
        config = ArtifactoryConfig(base_url="https://example.com/artifactory/")
        client = ArtifactoryClient(config=config)
        assert client._base_url == "https://example.com/artifactory"

    def test_build_headers_with_token(self) -> None:
        """Test headers include Bearer token when configured."""
        config = ArtifactoryConfig(base_url="https://example.com", api_token="my-token")
        client = ArtifactoryClient(config=config)
        headers = client._build_headers()
        assert headers == {"Authorization": "Bearer my-token"}

    def test_build_headers_without_token(self) -> None:
        """Test headers are empty when no token configured."""
        config = ArtifactoryConfig(base_url="https://example.com")
        client = ArtifactoryClient(config=config)
        headers = client._build_headers()
        assert headers == {}

    @patch("common.clients.artifactory_client.httpx.Client")
    def test_get_file_success(self, mock_client_cls: MagicMock) -> None:
        """Test successful file fetch."""
        mock_response = MagicMock()
        mock_response.text = "file content here"
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        config = ArtifactoryConfig(
            base_url="https://example.com/artifactory", api_token="tok"
        )
        client = ArtifactoryClient(config=config)
        result = client.get_file("repo/path/file.yml")

        assert result == "file content here"
        mock_client.get.assert_called_once_with(
            "https://example.com/artifactory/repo/path/file.yml",
            headers={"Authorization": "Bearer tok"},
        )
        mock_response.raise_for_status.assert_called_once()

    @patch("common.clients.artifactory_client.httpx.Client")
    def test_get_file_strips_leading_slash(self, mock_client_cls: MagicMock) -> None:
        """Test that leading slash in path is stripped."""
        mock_response = MagicMock()
        mock_response.text = "content"
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        config = ArtifactoryConfig(base_url="https://example.com/artifactory")
        client = ArtifactoryClient(config=config)
        client.get_file("/repo/path/file.yml")

        call_url = mock_client.get.call_args[0][0]
        assert call_url == "https://example.com/artifactory/repo/path/file.yml"

    @patch("common.clients.artifactory_client.httpx.Client")
    def test_get_file_raises_on_http_error(self, mock_client_cls: MagicMock) -> None:
        """Test that HTTP errors are raised."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Not Found",
            request=MagicMock(),
            response=MagicMock(status_code=404),
        )
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        config = ArtifactoryConfig(base_url="https://example.com/artifactory")
        client = ArtifactoryClient(config=config)

        with pytest.raises(httpx.HTTPStatusError):
            client.get_file("repo/missing.yml")


class TestCreateArtifactoryClient:
    """Test factory function."""

    def test_creates_client(self) -> None:
        """Test create_artifactory_client returns a configured client."""
        config = ArtifactoryConfig(base_url="https://example.com", api_token="tok")
        client = create_artifactory_client(config)
        assert isinstance(client, ArtifactoryClient)
        assert client._base_url == "https://example.com"
        assert client._token == "tok"
