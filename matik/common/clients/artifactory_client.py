"""Artifactory client for fetching file content."""

import logging
from dataclasses import dataclass, field

import httpx

from common.models.artifactory_config import ArtifactoryConfig

logger = logging.getLogger(__name__)


@dataclass
class ArtifactoryClient:
    """Client for fetching files from Artifactory.

    Example usage:
        from common.models.artifactory_config import ArtifactoryConfig

        config = ArtifactoryConfig(
            base_url="https://artifactory.airbnb.biz",
            api_token="my-token",
        )
        client = ArtifactoryClient(config=config)
        content = client.get_file("repo/path/to/file.txt")
    """

    config: ArtifactoryConfig

    _base_url: str = field(init=False)
    _token: str | None = field(init=False)

    def __post_init__(self) -> None:
        """Initialize the Artifactory client."""
        self._base_url = self.config.base_url.rstrip("/")
        self._token = self.config.api_token
        logger.info("Created Artifactory client for host: %s", self._base_url)

    def _build_headers(self) -> dict[str, str]:
        """Build HTTP headers for requests."""
        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def get_file(self, path: str) -> str:
        """Fetch file content from Artifactory.

        Args:
            path: Repo-relative path to the file
                  (e.g., "my-repo/path/to/file.txt").

        Returns:
            The file content as a string.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """
        url = f"{self._base_url}/{path.lstrip('/')}"
        logger.debug("Fetching file: %s", url)

        with httpx.Client(timeout=30.0) as client:
            response = client.get(url, headers=self._build_headers())
            response.raise_for_status()

        logger.info("Successfully fetched file: %s", path)
        return response.text


def create_artifactory_client(
    config: ArtifactoryConfig,
) -> ArtifactoryClient:
    """Create an Artifactory client from configuration.

    Args:
        config: ArtifactoryConfig with base_url and optional api_token.

    Returns:
        Configured ArtifactoryClient instance.
    """
    return ArtifactoryClient(config=config)
