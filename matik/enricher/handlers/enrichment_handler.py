"""Generic config-driven enrichment handler."""

import asyncio
import json
from typing import Any

from opentelemetry import trace

from common.clients.facade_client import (
    FacadeContentFilteredError,
    facade_system_message,
    facade_user_message,
)
from common.llm_tracing import traced_llm_operation
from common.models.enricher_config import EnricherConfig, SourceMappingEntry
from common.models.enricher_messages import EnrichmentRequest
from common.utils import log_utils
from common.utils.hash_utils import generate_string_hash

logger = log_utils.get_logger(__name__)

_tracer = trace.get_tracer(__name__)


def _canonical_entity_key(entity_id: dict[str, Any]) -> str:
    """Return a stable JSON string key for an entity_id dict.

    Args:
        entity_id: Entity primary key fields.

    Returns:
        JSON string with sorted keys for deterministic comparison.
    """
    return json.dumps(entity_id, sort_keys=True)


class EnrichmentHandler:
    """Generic enrichment handler that processes any source type using config-driven prompt mappings.

    For each EnrichmentRequest, looks up the source_type's mappings in config,
    computes content hashes per input key, fetches existing hashes from the
    Matik API (with an optional in-memory hash cache to avoid redundant API
    calls), and skips mappings whose input hashes are all unchanged. For
    changed or new mappings, calls Facade LLM and publishes the results with
    their computed hashes to the Scribe LLM queue.

    Adding support for a new source type requires only config changes (no code).
    """

    def __init__(
        self,
        facade_client: Any,
        sqs_client: Any,
        config: EnricherConfig,
        enricher_metrics: Any | None = None,
        matik_api_client: Any | None = None,
        hash_cache: Any | None = None,
    ) -> None:
        """Initialize with Facade client, SQS client, enricher config, and optional API client.

        Pre-builds all system prompts at init time by combining the general_prompt
        template with each mapping entry's source-specific instructions.

        Args:
            facade_client: Async FacadeClient instance for LLM calls.
            sqs_client: Async SQSClient instance for publishing to Scribe queue.
            config: EnricherConfig with source_mappings and prompt template.
            enricher_metrics: Optional EnricherMetrics instance for per-operation
                instrumentation. When provided, records each Facade call outcome
                with the actual enrichment_type label (output_field name).
            matik_api_client: Optional MatikApiClient for fetching existing hashes
                via POST /v1/enrichment/hashes. When None, all mappings are processed
                (safe default - no hash comparison is performed).
            hash_cache: Optional HashCache instance for in-memory caching of entity
                hashes. When provided, cache is checked before API calls and updated
                after successful Scribe publishes.
        """
        self._facade = facade_client
        self._sqs_client = sqs_client
        self._config = config
        self._metrics = enricher_metrics
        self._matik_api_client = matik_api_client
        self._hash_cache = hash_cache

        # Pre-build prompts: {source_type: [(mapping, full_system_prompt), ...]}
        self._compiled_mappings: dict[str, list[tuple[SourceMappingEntry, str]]] = {}
        for source_type, mappings in config.source_mappings.items():
            self._compiled_mappings[source_type] = [
                (m, config.build_prompt(m.prompt)) for m in mappings
            ]

        logger.info(
            "EnrichmentHandler initialized",
            source_types=list(self._compiled_mappings.keys()),
        )

    async def batch_fetch_hashes(
        self,
        requests: list[EnrichmentRequest],
    ) -> dict[str, dict[str, Any]]:
        """Batch-fetch hashes for multiple requests, using cache then API.

        Groups cache misses by source_type and makes one batch API call per group.
        Cache hits are returned directly without any API calls.

        Args:
            requests: List of EnrichmentRequest objects to fetch hashes for.

        Returns:
            Dict keyed by canonical entity_id JSON string -> hash dict.
        """
        results: dict[str, dict[str, Any]] = {}
        cache_misses: dict[
            str, list[dict[str, Any]]
        ] = {}  # source_type -> [entity_id, ...]

        for req in requests:
            canon_key = _canonical_entity_key(req.entity_id)
            if self._hash_cache is not None:
                cached = self._hash_cache.get(req.source_type, req.entity_id)
                if cached is not None:
                    results[canon_key] = cached
                    if self._metrics:
                        self._metrics.record_hash_cache("hit")
                    continue
                if self._metrics:
                    self._metrics.record_hash_cache("miss")
            cache_misses.setdefault(req.source_type, []).append(req.entity_id)

        # One batch API call per source_type, all fired in parallel
        if cache_misses:
            source_types = list(cache_misses.keys())
            fetch_results = await asyncio.gather(
                *[
                    self._batch_fetch_from_api(st, cache_misses[st])
                    for st in source_types
                ],
                return_exceptions=True,
            )
            for source_type, result in zip(source_types, fetch_results, strict=True):
                fetched = result if isinstance(result, dict) else {}
                for eid in cache_misses[source_type]:
                    canon_key = _canonical_entity_key(eid)
                    hashes = fetched.get(canon_key, {})
                    results[canon_key] = hashes
                    if self._hash_cache is not None:
                        self._hash_cache.put(source_type, eid, hashes)

        return results

    async def _batch_fetch_from_api(
        self,
        source_type: str,
        entity_ids: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Call POST /v1/enrichment/hashes/batch for one source_type.

        Falls back to parallel individual calls if the batch endpoint is
        unavailable or returns an error.

        Args:
            source_type: Source type for all entity_ids in this batch.
            entity_ids: List of entity primary key dicts to fetch hashes for.

        Returns:
            Dict keyed by canonical entity_id JSON string -> hash dict.
            Returns {} if no API client configured.
        """
        if self._matik_api_client is None:
            return {}
        with _tracer.start_as_current_span("batch_fetch_hashes") as span:
            span.set_attribute("source_type", source_type)
            span.set_attribute("entity_count", len(entity_ids))
            try:
                response_bytes = await asyncio.to_thread(
                    self._matik_api_client.post_json_request,
                    "/v1/enrichment/hashes/batch",
                    {"source_type": source_type, "entity_ids": entity_ids},
                )
                data: dict[str, Any] = json.loads(response_bytes)
                return dict(data.get("results", {}))
            except Exception:
                logger.warning(
                    "Batch hash fetch failed, falling back to parallel single calls",
                    source_type=source_type,
                    entity_count=len(entity_ids),
                    exc_info=True,
                )
                return await self._parallel_fetch_individual(source_type, entity_ids)

    async def _parallel_fetch_individual(
        self,
        source_type: str,
        entity_ids: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Fallback: fetch hashes individually in parallel via asyncio.gather.

        Args:
            source_type: Source type for all entity_ids.
            entity_ids: List of entity primary key dicts.

        Returns:
            Dict keyed by canonical entity_id JSON string -> hash dict.
        """

        async def _fetch_one(eid: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            hashes = await self._fetch_existing_hashes(source_type, eid)
            return _canonical_entity_key(eid), hashes

        results_list = await asyncio.gather(
            *[_fetch_one(eid) for eid in entity_ids],
            return_exceptions=True,
        )
        results: dict[str, dict[str, Any]] = {}
        for item in results_list:
            if isinstance(item, tuple):
                results[item[0]] = item[1]
        return results

    async def enrich(
        self,
        request: EnrichmentRequest,
        existing_hashes: dict[str, Any] | None = None,
    ) -> None:
        """Process enrichment mappings for the request, skipping unchanged fields.

        For each mapping entry configured for request.source_type:
          1. Computes a SHA256 hash per input key from request.content.
          2. Fetches existing hashes from DB via POST /v1/enrichment/hashes
             (skipped when existing_hashes is provided as a pre-fetched dict).
          3. Skips mappings where ALL input key hashes match existing values.
          4. Calls Facade LLM for mappings with any changed or missing hash.
          5. If all mappings were skipped, returns without publishing to Scribe.
          6. Otherwise publishes only the changed LLM outputs and their hashes
             (keyed by DB column name from hash_fields) to the Scribe LLM queue.
          7. Updates the hash cache with the new hashes after publish.

        Args:
            request: The EnrichmentRequest to process.
            existing_hashes: Pre-fetched hash dict (from batch_fetch_hashes).
                When provided, skips the individual API fetch call.

        Raises:
            ContentFilteredError: If any Facade call is blocked by a content filter.
            FacadeBadRequestError: If Facade returns a 400 Bad Request.
            Exception: Other exceptions propagate for retry logic in the consumer loop.
        """
        compiled = self._compiled_mappings[request.source_type]

        # Compute one combined hash per mapping by concatenating all input values
        # with '||'. Empty/missing fields use empty string so the hash is always
        # stored, preventing indefinite re-processing of empty content.
        mapping_hashes: dict[str, str] = {}
        for mapping, _ in compiled:
            combined = "||".join(
                request.content.get(k, "") or "" for k in mapping.input_keys
            )
            mapping_hashes[mapping.output_field] = generate_string_hash(combined)

        # Use pre-fetched hashes if provided, otherwise fetch individually
        if existing_hashes is None:
            existing_hashes = await self._fetch_existing_hashes(
                request.source_type, request.entity_id
            )

        # Process only mapping entries where the combined hash changed
        results: dict[str, str | None] = {}
        changed_hashes: dict[str, str] = {}

        for mapping, full_prompt in compiled:
            current_hash = mapping_hashes[mapping.output_field]
            existing_hash = existing_hashes.get(mapping.hash_field)

            if current_hash == existing_hash:
                # Content unchanged — skip the Facade/LLM call (a saved call).
                if self._metrics:
                    self._metrics.record_enrichment_cache(
                        request.source_type, mapping.output_field, "hit"
                    )
                logger.info(
                    "Hashes unchanged, skipping mapping",
                    output_field=mapping.output_field,
                    source_type=request.source_type,
                )
                continue

            # "miss" = a prior hash existed but changed; "new" = first time seen.
            if self._metrics:
                self._metrics.record_enrichment_cache(
                    request.source_type,
                    mapping.output_field,
                    "miss" if existing_hash else "new",
                )

            result = await self._call_facade(
                full_prompt,
                request.content,
                mapping.input_keys,
                mapping.output_field,
                request.source_type,
                request.entity_id,
            )
            results[mapping.output_field] = result
            changed_hashes[mapping.hash_field] = mapping_hashes[mapping.output_field]

        if not results:
            logger.info(
                "All hashes match, skipping enrichment entirely",
                source_type=request.source_type,
                task_id=request.task_id,
            )
            return

        scribe_message = {
            "source_type": request.source_type,
            "message_type": "enrichment",
            "entity_id": request.entity_id,
            "updates": results,
            "hashes": changed_hashes,
            # Staleness guard (ADR 024): forward entered_at unchanged from the
            # inbound EnrichmentRequest — never re-stamp to "now" here. A
            # message stuck in the Enricher's own DLQ must keep looking as
            # old as it actually is, or a stale retry could beat a message
            # that flowed through cleanly in the meantime.
            "entered_at": (
                request.entered_at.isoformat() if request.entered_at else None
            ),
        }
        await self._sqs_client.send_message(
            queue_url=self._config.scribe_llm_queue_url,
            message_body=json.dumps(scribe_message),
        )
        logger.info(
            "Enrichment published to Scribe queue",
            source_type=request.source_type,
            task_id=request.task_id,
            output_fields=list(results.keys()),
        )

        # Update cache with the newly computed hashes
        if self._hash_cache is not None and changed_hashes:
            self._hash_cache.update(
                request.source_type, request.entity_id, changed_hashes
            )

    async def _fetch_existing_hashes(
        self, source_type: str, entity_id: dict[str, Any]
    ) -> dict[str, Any]:
        """Fetch existing hash values for an entity from cache then API.

        Checks the in-memory cache first. On a cache miss, calls
        POST /v1/enrichment/hashes with the source_type and entity_id.
        On any API error (including missing client), logs a warning and returns
        an empty dict - this causes all mappings to be processed (safe default).

        Args:
            source_type: The source type for the entity (e.g., "incidentio").
            entity_id: The entity's primary key fields (e.g., {"incident_id": "123"}).

        Returns:
            Dict of existing hash values keyed by DB column name, or {} on error.
        """
        if self._hash_cache is not None:
            cached = self._hash_cache.get(source_type, entity_id)
            if cached is not None:
                if self._metrics:
                    self._metrics.record_hash_cache("hit")
                return dict(cached)
            if self._metrics:
                self._metrics.record_hash_cache("miss")

        if self._matik_api_client is None:
            return {}
        with _tracer.start_as_current_span("fetch_existing_hashes") as span:
            span.set_attribute("source_type", source_type)
            try:
                response_bytes = await asyncio.to_thread(
                    self._matik_api_client.post_json_request,
                    "/v1/enrichment/hashes",
                    {"source_type": source_type, "entity_id": entity_id},
                )
                return dict(json.loads(response_bytes))
            except Exception:
                logger.warning(
                    "Failed to fetch existing hashes, proceeding with full enrichment",
                    source_type=source_type,
                    exc_info=True,
                )
                return {}

    async def _call_facade(
        self,
        system_prompt: str,
        content: dict[str, Any],
        input_keys: list[str],
        operation: str,
        source_type: str,
        entity_id: dict[str, Any],
    ) -> str | None:
        """Call Facade LLM and return the response content.

        Gathers values for all input_keys from content, assembles them into a
        labeled user message, then calls Facade with the pre-built system prompt.
        Records per-operation metrics if enricher_metrics was provided at init.

        Args:
            system_prompt: The fully assembled system prompt for this mapping.
            content: The content dict from the EnrichmentRequest.
            input_keys: Keys to extract and combine from content.
            operation: Operation name equal to output_field (e.g., "root_cause_summary").
            source_type: The source type for metrics labeling (e.g., "incidentio").
            entity_id: The entity's primary key fields (e.g., {"incident_id": "123"}),
                recorded on the trace so it's identifiable in Braintrust.

        Returns:
            The LLM response string, or None if the response was empty.

        Raises:
            ContentFilteredError: Wraps FacadeContentFilteredError.
            FacadeBadRequestError: Propagated as-is (non-retriable).
            Exception: Other errors propagated for retry.
        """
        from enricher.exceptions import ContentFilteredError

        input_parts = []
        for key in input_keys:
            value = content.get(key) or ""  # Handle None values explicitly
            label = key.replace("_", " ").title()
            input_parts.append(f"{label}:\n{value}")
        user_content = "\n\n".join(input_parts)

        # entity_id's primary key (e.g. incident_id) is the internal DB/API
        # lookup key, not necessarily what a human recognizes. When the source
        # also forwarded a human-facing reference (e.g. incidentio's "INC-1234"
        # reference_id), surface it on the trace too, without replacing entity_id.
        trace_entity_id = entity_id
        if reference_id := content.get("reference_id"):
            trace_entity_id = {**entity_id, "reference_id": reference_id}

        try:
            with traced_llm_operation(
                operation, source=source_type, entity_id=trace_entity_id
            ):
                response = await self._facade.send_message_with_retry(
                    model=None,
                    messages=[
                        facade_system_message(system_prompt),
                        facade_user_message(user_content),
                    ],
                    operation=operation,
                )
            if self._metrics:
                self._metrics.record_enrichment_operation(
                    source_type, operation, success=True
                )
            return response or None
        except FacadeContentFilteredError as e:
            logger.warning(
                "Content filtered by Facade",
                operation=operation,
                input_keys=input_keys,
            )
            if self._metrics:
                self._metrics.record_enrichment_operation(
                    source_type, operation, success=False, error=e
                )
            raise ContentFilteredError(str(e)) from e
        except Exception as e:
            if self._metrics:
                self._metrics.record_enrichment_operation(
                    source_type, operation, success=False, error=e
                )
            raise
