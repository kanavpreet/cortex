"""Greenroom/Backstage client for interacting with the catalog API."""

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from common.models.common_config import CommonConfig
from common.models.greenroom_config import GreenroomConfig
from common.models.greenroom_entity import GreenroomEntity, GreenroomEntityRelation
from common.utils.env_utils import is_local_environment

logger = logging.getLogger(__name__)

# Default pagination limits
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_RESULTS = 1000
MAX_PAGES = 100  # Backstop to prevent infinite loops


@dataclass
class GreenroomClient:
    """Client for interacting with the Greenroom/Backstage API.

    Supports two authentication modes:
    1. Local (laptop) mode: Uses IAP token + Backstage token for human users
    2. Kubernetes mode: Uses static Bearer token for service-to-service via AirMesh

    Example usage:
        from common.config import load_config

        config = load_config("config/matik-service-config.yml")
        client = GreenroomClient(
            greenroom_config=config.greenroom,
            common_config=config.common,
        )

        entity = client.get_entity_by_name("component", "default", "my-service")
    """

    greenroom_config: GreenroomConfig
    common_config: CommonConfig | None = None

    _host: str = field(init=False)
    _backstage_token: str = field(init=False)
    _iap_token: str | None = field(init=False)
    _is_local: bool = field(init=False)

    def __post_init__(self) -> None:
        """Initialize the Greenroom client with authentication."""
        self._is_local = is_local_environment(self.common_config)

        if self._is_local:
            # Local development mode: Use IAP token and fetch Backstage token
            logger.info("Running in local mode - using IAP authentication")

            self._host = "https://developers.a.musta.ch"
            self._iap_token = self.greenroom_config.iap_token

            if not self._iap_token:
                raise ValueError(
                    "iap_token must be set in config for local development. "
                    "Get token via: iap-auth https://developers.a.musta.ch"
                )

            # Fetch Backstage token using IAP token
            logger.debug("Fetching Backstage token using IAP token")
            self._backstage_token = self._get_backstage_token()
            logger.info("Successfully obtained Backstage token")

        else:
            # Kubernetes mode: Use static API token and AirMesh endpoint
            env_name = (
                self.common_config.environment if self.common_config else "unknown"
            )
            logger.info("Running in Kubernetes mode (environment: %s)", env_name)

            self._host = self.greenroom_config.host
            self._iap_token = None

            if not self.greenroom_config.api_token:
                raise ValueError("api_token must be set in config for Kubernetes mode")

            self._backstage_token = self.greenroom_config.api_token

        logger.info(
            "Successfully created Greenroom client for host: %s (local mode: %s)",
            self._host,
            self._is_local,
        )

    def _get_backstage_token(self) -> str:
        """Fetch a Backstage identity token using an IAP token.

        This is required for local development when accessing Greenroom
        via the public endpoint.

        Returns:
            The Backstage token string.

        Raises:
            Exception: If the request fails.
        """
        refresh_url = f"{self._host}/api/auth/mdap/refresh"

        headers: dict[str, str] = {}
        if self._iap_token:
            headers["Proxy-Authorization"] = f"Bearer {self._iap_token}"

        with httpx.Client(timeout=30.0) as client:
            response = client.get(refresh_url, headers=headers)
            response.raise_for_status()
            data = response.json()

        token = data.get("backstageIdentity", {}).get("token", "")
        if not token:
            raise ValueError("Failed to get Backstage token from response")

        return str(token)

    def _build_headers(self) -> dict[str, str]:
        """Build HTTP headers for requests."""
        headers = {
            "Authorization": f"Bearer {self._backstage_token}",
            "Content-Type": "application/json",
        }

        # For local mode, add IAP token
        if self._is_local and self._iap_token:
            headers["Proxy-Authorization"] = f"Bearer {self._iap_token}"

        return headers

    def get_entity_by_name(
        self, kind: str, namespace: str, name: str
    ) -> GreenroomEntity:
        """Retrieve a specific entity by kind, namespace, and name.

        Args:
            kind: The entity kind (e.g., "component", "service", "api")
            namespace: The entity namespace (e.g., "default")
            name: The entity name

        Returns:
            GreenroomEntity model.

        Raises:
            httpx.HTTPStatusError: If the entity is not found or request fails.

        Example:
            entity = client.get_entity_by_name("component", "default", "my-service")
        """
        logger.debug("Getting entity: %s:%s/%s", kind, namespace, name)

        url = f"{self._host}/api/catalog/entities/by-name/{kind}/{namespace}/{name}"

        with httpx.Client(timeout=30.0) as client:
            response = client.get(url, headers=self._build_headers())
            response.raise_for_status()
            data = response.json()

        logger.info("Successfully retrieved entity: %s:%s/%s", kind, namespace, name)
        return self._convert_to_matik_entity(data)

    def get_entity_by_uid(self, uid: str) -> GreenroomEntity:
        """Retrieve a specific entity by its unique identifier.

        Args:
            uid: The entity's unique identifier

        Returns:
            GreenroomEntity model.

        Raises:
            httpx.HTTPStatusError: If the entity is not found or request fails.

        Example:
            entity = client.get_entity_by_uid("abc123-def456-ghi789")
        """
        logger.debug("Getting entity by UID: %s", uid)

        url = f"{self._host}/api/catalog/entities/by-uid/{uid}"

        with httpx.Client(timeout=30.0) as client:
            response = client.get(url, headers=self._build_headers())
            response.raise_for_status()
            data = response.json()

        logger.info("Successfully retrieved entity by UID: %s", uid)
        return self._convert_to_matik_entity(data)

    def query_entities(
        self,
        filters: list[str] | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
        max_results: int = DEFAULT_MAX_RESULTS,
    ) -> list[GreenroomEntity]:
        """Retrieve entities matching the specified filters with a safe default limit.

        This method wraps query_entities_with_limit with a default maximum of 1000 results.

        Args:
            filters: Array of filter strings in Greenroom query format
                    (e.g., ["kind=component", "metadata.namespace=default"])
            page_size: Number of results per page (recommended: 100)

        Returns:
            Up to 1000 entities matching the filters.

        Example:
            filters = ["kind=component", "metadata.namespace=default"]
            entities = client.query_entities(filters)
        """
        logger.debug(
            "Querying entities with filters: %s, page_size: %d, max_results: %d",
            filters,
            page_size,
            max_results,
        )

        all_entities: list[GreenroomEntity] = []
        cursor: str | None = None
        page_count = 0

        while True:
            page_count += 1
            logger.debug("Fetching page %d", page_count)

            # Backstop: Stop if we've fetched too many pages
            if page_count > MAX_PAGES:
                logger.warning(
                    "Reached maximum page limit (%d pages), stopping pagination. "
                    "Consider using more specific filters.",
                    MAX_PAGES,
                )
                break

            # Build request URL and params
            url = f"{self._host}/api/catalog/entities"
            params: dict[str, Any] = {"limit": page_size}

            if filters:
                params["filter"] = filters

            if cursor:
                params["cursor"] = cursor

            with httpx.Client(timeout=30.0) as client:
                response = client.get(url, headers=self._build_headers(), params=params)
                response.raise_for_status()
                data = response.json()

            # Process results
            items = data.get("items", [])
            for item in items:
                # Primary limit: Stop if we've collected enough results
                if max_results > 0 and len(all_entities) >= max_results:
                    logger.info(
                        "Reached maximum result limit (%d results), stopping pagination",
                        max_results,
                    )
                    return all_entities

                all_entities.append(self._convert_to_matik_entity(item))

            logger.debug(
                "Retrieved %d entities on page %d, total so far: %d",
                len(items),
                page_count,
                len(all_entities),
            )

            # Check if there are more pages
            page_info = data.get("pageInfo", {})
            if not page_info.get("hasNextCursor", False):
                break

            cursor = page_info.get("nextCursor")

        logger.info(
            "Successfully queried %d entities across %d pages",
            len(all_entities),
            page_count,
        )
        return all_entities

    def _convert_to_matik_entity(self, data: dict[str, Any]) -> GreenroomEntity:
        """Convert a Greenroom API entity to Matik's internal model.

        This flattens the nested structure for easier use within Matik services.
        """
        if not data:
            raise ValueError("Cannot convert empty entity data")

        metadata = data.get("metadata", {})

        # Convert relations
        relations: list[GreenroomEntityRelation] | None = None
        raw_relations = data.get("relations")
        if raw_relations:
            relations = [
                GreenroomEntityRelation(
                    type=rel.get("type", ""),
                    target_ref=rel.get("targetRef", ""),
                )
                for rel in raw_relations
            ]

        return GreenroomEntity(
            api_version=data.get("apiVersion", ""),
            kind=data.get("kind", ""),
            name=metadata.get("name", ""),
            namespace=metadata.get("namespace"),
            uid=metadata.get("uid"),
            etag=metadata.get("etag"),
            title=metadata.get("title"),
            description=metadata.get("description"),
            labels=metadata.get("labels"),
            annotations=metadata.get("annotations"),
            tags=metadata.get("tags"),
            spec=data.get("spec"),
            relations=relations,
        )


def create_greenroom_client(
    greenroom_config: GreenroomConfig,
    common_config: CommonConfig | None = None,
) -> GreenroomClient:
    """Create a Greenroom client from configuration objects.

    Args:
        greenroom_config: GreenroomConfig from MatikConfig.greenroom
        common_config: CommonConfig from MatikConfig.common (optional)

    Returns:
        Configured GreenroomClient instance.

    Example:
        from common.config import load_config

        config = load_config("config/matik-service-config.yml")
        client = create_greenroom_client(
            greenroom_config=config.greenroom,
            common_config=config.common,
        )
    """
    return GreenroomClient(
        greenroom_config=greenroom_config,
        common_config=common_config,
    )
