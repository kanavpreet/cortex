"""Incident.io client for interacting with the Incident.io API."""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from common.metrics import ClientMetrics
from common.models.incidentio_config import (
    INCIDENTIO_API_VERSION,
    INCIDENTIO_MAX_PAGE_SIZE,
    INCIDENTIO_UPDATES_MAX_PAGE_SIZE,
    IncidentIOConfig,
)
from common.models.incidentio_incident import IncidentIOIncident
from common.utils import log_utils
from common.utils.incidentio_utils import (
    build_incident_from_payload,
    extract_custom_fields,
    extract_timestamps,
)
from common.utils.retry_utils import get_backoff_delay

logger = log_utils.get_logger(__name__)

# Retryable HTTP status codes
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


@dataclass
class IncidentWithRawFields:
    """Wrapper containing incident model and raw fields for LLM processing.

    The raw fields (name, summary, resolution_statement) are not stored in DB
    but are used for LLM summarization to generate description_summary
    and root_cause_summary.
    """

    incident: IncidentIOIncident
    name: str | None = None  # Raw incident name for LLM → description_summary
    summary: str | None = (
        None  # Raw summary for LLM → description_summary + root_cause_summary
    )
    resolution_statement: str | None = (
        None  # Raw statement for LLM → root_cause_summary
    )


@dataclass
class IncidentIOClient:
    """Async client for interacting with the Incident.io API.

    Example usage:
        from common.config import load_config

        config = load_config("config/matik-historian-config.yml")
        client = IncidentIOClient(incidentio_config=config.incidentio)

        incidents = await client.list_incidents()

    For all incidents with pagination:
        all_incidents = await client.list_all_incidents()
    """

    incidentio_config: IncidentIOConfig
    client_metrics: ClientMetrics | None = None

    _headers: dict[str, str] = field(init=False)

    def __post_init__(self) -> None:
        """Initialize the client after dataclass construction."""
        self._headers = {
            "Authorization": f"Bearer {self.incidentio_config.api_key}",
            "Content-Type": "application/json",
        }
        logger.info(
            "created IncidentIOClient", base_url=self.incidentio_config.base_url
        )

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        endpoint: str = "",
    ) -> dict[str, Any]:
        """Make HTTP request with exponential backoff retry.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Request URL
            params: Query parameters
            endpoint: API endpoint path for metrics (e.g., "/v2/incidents")

        Returns:
            JSON response as dictionary.

        Raises:
            httpx.HTTPStatusError: If request fails after all retries.
        """
        last_err: Exception | None = None
        max_retries = self.incidentio_config.max_retries
        backoff_delays = self.incidentio_config.backoff_delays
        timeout = self.incidentio_config.timeout
        start_time = time.perf_counter()

        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(max_retries + 1):
                if attempt > 0:
                    delay = get_backoff_delay(attempt - 1, backoff_delays)
                    logger.warning(
                        "retrying request",
                        attempt=attempt + 1,
                        max_attempts=max_retries + 1,
                        delay_seconds=delay,
                    )
                    await asyncio.sleep(delay)
                    # Record retry attempt
                    if self.client_metrics and endpoint:
                        self.client_metrics.record_retry(method, endpoint, attempt)

                try:
                    if method.upper() == "GET":
                        response = await client.get(
                            url, headers=self._headers, params=params
                        )
                    else:
                        response = await client.request(
                            method, url, headers=self._headers, params=params
                        )

                    # Check for retryable status codes
                    if response.status_code in RETRYABLE_STATUS_CODES:
                        last_err = httpx.HTTPStatusError(
                            f"Request failed with status {response.status_code}",
                            request=response.request,
                            response=response,
                        )
                        logger.warning(
                            "retryable error received",
                            status_code=response.status_code,
                            url=url,
                        )
                        continue

                    response.raise_for_status()

                    # Record successful request
                    if self.client_metrics and endpoint:
                        duration = time.perf_counter() - start_time
                        self.client_metrics.record_request(
                            method, endpoint, duration, response.status_code
                        )

                    result: dict[str, Any] = response.json()
                    return result

                except httpx.TimeoutException as err:
                    last_err = err
                    logger.warning("request timeout", url=url, error=str(err))
                except httpx.ConnectError as err:
                    last_err = err
                    logger.warning("connection error", url=url, error=str(err))
                except httpx.HTTPStatusError:
                    raise  # Non-retryable HTTP errors

        # Record failed request after all retries exhausted
        if self.client_metrics and endpoint:
            duration = time.perf_counter() - start_time
            self.client_metrics.record_request(
                method, endpoint, duration, 500, last_err
            )

        if last_err:
            logger.error(
                "request failed after retries",
                url=url,
                attempts=max_retries + 1,
                error=str(last_err),
            )
            raise last_err

        raise RuntimeError(f"Request failed after {max_retries + 1} attempts")

    def _extract_timestamps(
        self, timestamps: list[dict[str, Any]] | None
    ) -> dict[str, datetime | None]:
        """Delegate to the shared util — see `common.utils.incidentio_utils`."""
        return extract_timestamps(timestamps)

    def _extract_custom_fields(
        self, entries: list[dict[str, Any]] | None
    ) -> dict[str, Any]:
        """Delegate to the shared util — see `common.utils.incidentio_utils`."""
        return extract_custom_fields(entries)

    def _process_incident(self, raw: dict[str, Any]) -> IncidentWithRawFields:
        """Transform raw API incident data into IncidentWithRawFields wrapper."""
        incident, name, summary, resolution_statement = build_incident_from_payload(raw)

        logger.debug(
            "processed incident",
            incident_id=incident.incident_id,
            reference_id=incident.reference_id,
            has_name=name is not None,
            has_summary=summary is not None,
            has_resolution_statement=resolution_statement is not None,
        )

        return IncidentWithRawFields(
            incident=incident,
            name=name,
            summary=summary,
            resolution_statement=resolution_statement,
        )

    async def list_incidents(
        self,
        after: str | None = None,
        page_size: int | None = None,
        updated_at_gte: str | None = None,
        created_at_gte: str | None = None,
        incident_type_ids: list[str] | None = None,
    ) -> tuple[list[IncidentWithRawFields], int, str | None]:
        """Retrieve a page of incidents from Incident.io.

        Args:
            after: Cursor for pagination (incident ID to start after)
            page_size: Number of incidents per page (defaults to config page_size)
            updated_at_gte: Filter incidents updated on or after this date (YYYY-MM-DD)
            created_at_gte: Filter incidents created on or after this date (YYYY-MM-DD)
            incident_type_ids: Filter to incidents of these type IDs only

        Returns:
            Tuple of (incidents, raw_count, last_raw_id) where:
            - incidents: List of IncidentWithRawFields (public incidents only)
            - raw_count: Number of incidents returned by API before filtering
            - last_raw_id: ID of last incident in raw response (for pagination)

        Raises:
            httpx.HTTPStatusError: If the API request fails.

        Example:
            incidents, raw_count, last_id = await client.list_incidents()
            # Get next page using last_id for cursor
            if last_id:
                next_page, _, next_last_id = await client.list_incidents(after=last_id)
        """
        # Use config defaults if not specified
        if page_size is None:
            page_size = self.incidentio_config.page_size

        logger.info(
            "listing incidents",
            after=after or "(start)",
            page_size=page_size,
            updated_at_gte=updated_at_gte,
            created_at_gte=created_at_gte,
            incident_type_ids=incident_type_ids,
        )

        # Clamp page_size to API maximum
        page_size = min(page_size, INCIDENTIO_MAX_PAGE_SIZE)

        endpoint = f"/{INCIDENTIO_API_VERSION}/incidents"
        base_url = self.incidentio_config.base_url
        url = f"{base_url}{endpoint}"
        params: dict[str, Any] = {"page_size": page_size}
        if after:
            params["after"] = after
        if updated_at_gte:
            params["updated_at[gte]"] = updated_at_gte
        if created_at_gte:
            params["created_at[gte]"] = created_at_gte
        if incident_type_ids:
            params["incident_type[one_of]"] = incident_type_ids

        data = await self._request_with_retry("GET", url, params, endpoint=endpoint)

        incidents_data = data.get("incidents", [])
        raw_count = len(incidents_data)
        logger.debug("fetched incidents", count=raw_count)

        # Get last raw ID for pagination (before filtering)
        last_raw_id = incidents_data[-1].get("id") if incidents_data else None

        # Process each incident, filtering out non-public incidents
        incidents: list[IncidentWithRawFields] = []
        filtered_count = 0
        for raw in incidents_data:
            # Skip non-public incidents
            if raw.get("visibility") != "public":
                filtered_count += 1
                continue
            incident_with_raw = self._process_incident(raw)
            incidents.append(incident_with_raw)

        if filtered_count > 0:
            logger.debug(
                "filtered non-public incidents",
                filtered=filtered_count,
                remaining=len(incidents),
            )

        return incidents, raw_count, last_raw_id

    async def get_incident(self, incident_id: str) -> dict[str, Any]:
        """Fetch a single incident's full record, including its narrative summary.

        Unlike ``list_incidents``, the response is returned raw — NOT parsed into
        ``IncidentWithRawFields``. The ``summary`` field carries incident.io's
        human/AI-written problem-impact-cause-resolution narrative — never
        persisted to the DB (only the derived ``root_cause_summary`` is) — but it
        does not reliably contain a specific PR/TCMR citation. For that, pair this
        with ``list_incident_updates``: a concrete "traced to PR #N" citation
        typically lives in an update's ``message``, not in this endpoint's
        ``summary``.

        Args:
            incident_id: Incident.io's internal incident ID (e.g.
                "01K3H5K30V3TECAF9G2HD1X5ZB"), NOT the human reference (INC-1234).
                Use ``IncidentIOIncident.incident_id`` from an already-fetched
                record.

        Returns:
            Raw JSON response as a dictionary (``{"incident": {...}}``).

        Raises:
            httpx.HTTPStatusError: If the API request fails.
        """
        endpoint = f"/{INCIDENTIO_API_VERSION}/incidents/{incident_id}"
        base_url = self.incidentio_config.base_url
        url = f"{base_url}{endpoint}"

        logger.info("fetching incident", incident_id=incident_id)
        return await self._request_with_retry("GET", url, endpoint=endpoint)

    async def list_incident_updates(self, incident_id: str) -> list[dict[str, Any]]:
        """Fetch every status update posted on an incident, oldest first.

        This is the source of concrete PR/TCMR citations: incident.io's own
        ``summary`` field carries a narrative but not reliably a specific
        identifier, while individual update ``message`` text (written by
        responders as the incident progressed) is where citations like
        "traced to PR #209" actually appear. Not every update has a
        ``message`` (many are pure status/severity transitions); callers
        should skip entries where it's absent or empty.

        Args:
            incident_id: Incident.io's internal incident ID, NOT the human
                reference. See ``get_incident``.

        Returns:
            Raw update dictionaries, oldest first (the API returns newest first;
            this reverses it so source text reads in chronological order).

        Raises:
            httpx.HTTPStatusError: If any page request fails.
        """
        endpoint = f"/{INCIDENTIO_API_VERSION}/incident_updates"
        base_url = self.incidentio_config.base_url
        url = f"{base_url}{endpoint}"

        all_updates: list[dict[str, Any]] = []
        cursor: str | None = None

        while True:
            params: dict[str, Any] = {
                "incident_id": incident_id,
                "page_size": INCIDENTIO_UPDATES_MAX_PAGE_SIZE,
            }
            if cursor:
                params["after"] = cursor

            data = await self._request_with_retry("GET", url, params, endpoint=endpoint)
            page_updates = data.get("incident_updates", [])
            all_updates.extend(page_updates)

            cursor = (data.get("pagination_meta") or {}).get("after")
            if not cursor or not page_updates:
                break

        all_updates.reverse()
        return all_updates

    async def list_all_incidents(
        self,
        page_size: int | None = None,
        last_recorded: str | None = None,
        incident_type_ids: list[str] | None = None,
        updated_at_gte: str | None = None,
    ) -> list[IncidentWithRawFields]:
        """Retrieve all incidents by paginating through all pages.

        Args:
            page_size: Number of incidents per page (defaults to config page_size)
            last_recorded: Incident ID to start after (for incremental fetches)
            incident_type_ids: Filter to incidents of these type IDs only
            updated_at_gte: Filter incidents updated on or after this date (YYYY-MM-DD)

        Returns:
            List of all IncidentWithRawFields (incident model + raw fields for LLM).

        Raises:
            httpx.HTTPStatusError: If any API request fails.

        Example:
            # Fetch all incidents
            all_incidents = await client.list_all_incidents()

            # Incremental fetch from last known incident
            new_incidents = await client.list_all_incidents(
                last_recorded="01K3H5K30V3TECAF9G2HD1X5ZB",
            )
        """
        # Use config defaults if not specified
        if page_size is None:
            page_size = self.incidentio_config.page_size

        logger.info(
            "listing all incidents",
            page_size=page_size,
            last_recorded=last_recorded or "(start)",
            incident_type_ids=incident_type_ids,
            updated_at_gte=updated_at_gte,
        )

        all_incidents: list[IncidentWithRawFields] = []
        cursor = last_recorded
        page_count = 0

        while True:
            page_count += 1
            logger.debug("fetching page", page_number=page_count)

            incidents, raw_count, last_raw_id = await self.list_incidents(
                after=cursor,
                page_size=page_size,
                incident_type_ids=incident_type_ids,
                updated_at_gte=updated_at_gte,
            )

            all_incidents.extend(incidents)
            logger.debug(
                "page fetched",
                page_number=page_count,
                page_incidents=len(incidents),
                raw_count=raw_count,
                total_incidents=len(all_incidents),
            )

            # If API returned fewer than page_size, we've reached the last page
            # Use raw_count (before filtering) to determine if more pages exist
            if raw_count < page_size:
                break

            # Update cursor to last incident ID from raw response (before filtering)
            # This ensures correct pagination even if all incidents in a page are filtered
            cursor = last_raw_id

        logger.info(
            "finished listing all incidents",
            total_incidents=len(all_incidents),
            pages=page_count,
        )

        return all_incidents


@dataclass
class IncidentIOClientSync:
    """Synchronous client for interacting with the Incident.io API.

    This is a sync wrapper around IncidentIOClient for use in non-async contexts.

    Example usage:
        from common.config import load_config

        config = load_config("config/matik-historian-config.yml")
        client = IncidentIOClientSync(incidentio_config=config.incidentio)

        incidents = client.list_incidents()
    """

    incidentio_config: IncidentIOConfig
    client_metrics: ClientMetrics | None = None

    _async_client: IncidentIOClient = field(init=False)

    def __post_init__(self) -> None:
        """Initialize the sync wrapper with an async client."""
        self._async_client = IncidentIOClient(
            incidentio_config=self.incidentio_config,
            client_metrics=self.client_metrics,
        )
        logger.info(
            "created IncidentIOClientSync",
            base_url=self.incidentio_config.base_url,
        )

    def list_incidents(
        self,
        after: str | None = None,
        page_size: int | None = None,
        incident_type_ids: list[str] | None = None,
    ) -> list[IncidentWithRawFields]:
        """Sync wrapper for list_incidents.

        Args:
            after: Cursor for pagination (incident ID to start after)
            page_size: Number of incidents per page (defaults to config page_size)
            incident_type_ids: Filter to incidents of these type IDs only

        Returns:
            List of IncidentWithRawFields (incident model + raw fields for LLM).

        Raises:
            httpx.HTTPStatusError: If the API request fails.
        """
        incidents, _, _ = asyncio.run(
            self._async_client.list_incidents(
                after=after,
                page_size=page_size,
                incident_type_ids=incident_type_ids,
            )
        )
        return incidents

    def list_all_incidents(
        self,
        page_size: int | None = None,
        last_recorded: str | None = None,
        incident_type_ids: list[str] | None = None,
        updated_at_gte: str | None = None,
    ) -> list[IncidentWithRawFields]:
        """Sync wrapper for list_all_incidents.

        Args:
            page_size: Number of incidents per page (defaults to config page_size)
            last_recorded: Incident ID to start after (for incremental fetches)
            incident_type_ids: Filter to incidents of these type IDs only
            updated_at_gte: Filter incidents updated on or after this date (YYYY-MM-DD)

        Returns:
            List of all IncidentWithRawFields (incident model + raw fields for LLM).

        Raises:
            httpx.HTTPStatusError: If any API request fails.
        """
        return asyncio.run(
            self._async_client.list_all_incidents(
                page_size=page_size,
                last_recorded=last_recorded,
                incident_type_ids=incident_type_ids,
                updated_at_gte=updated_at_gte,
            )
        )

    def get_incident(self, incident_id: str) -> dict[str, Any]:
        """Sync wrapper for get_incident.

        Args:
            incident_id: Incident.io's internal incident ID, NOT the human
                reference. See ``IncidentIOClient.get_incident``.

        Returns:
            Raw JSON response as a dictionary.

        Raises:
            httpx.HTTPStatusError: If the API request fails.
        """
        return asyncio.run(self._async_client.get_incident(incident_id))

    def list_incident_updates(self, incident_id: str) -> list[dict[str, Any]]:
        """Sync wrapper for list_incident_updates.

        Args:
            incident_id: Incident.io's internal incident ID, NOT the human
                reference. See ``IncidentIOClient.list_incident_updates``.

        Returns:
            Raw update dictionaries, oldest first.

        Raises:
            httpx.HTTPStatusError: If any page request fails.
        """
        return asyncio.run(self._async_client.list_incident_updates(incident_id))


def create_incidentio_client(
    incidentio_config: IncidentIOConfig,
    client_metrics: ClientMetrics | None = None,
) -> IncidentIOClient:
    """Create an async IncidentIOClient from configuration.

    Args:
        incidentio_config: IncidentIOConfig from MatikConfig.incidentio
        client_metrics: ClientMetrics for request instrumentation (optional)

    Returns:
        Configured IncidentIOClient instance.

    Example:
        from common.config import load_config

        config = load_config("config/matik-historian-config.yml")
        client = create_incidentio_client(incidentio_config=config.incidentio)
    """
    return IncidentIOClient(
        incidentio_config=incidentio_config, client_metrics=client_metrics
    )


def create_incidentio_client_sync(
    incidentio_config: IncidentIOConfig,
    client_metrics: ClientMetrics | None = None,
) -> IncidentIOClientSync:
    """Create a sync IncidentIOClientSync from configuration.

    Args:
        incidentio_config: IncidentIOConfig from MatikConfig.incidentio
        client_metrics: ClientMetrics for request instrumentation (optional)

    Returns:
        Configured IncidentIOClientSync instance.

    Example:
        from common.config import load_config

        config = load_config("config/matik-historian-config.yml")
        client = create_incidentio_client_sync(incidentio_config=config.incidentio)
    """
    return IncidentIOClientSync(
        incidentio_config=incidentio_config, client_metrics=client_metrics
    )
