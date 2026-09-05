"""In-memory TTL cache for enrichment entity hashes."""

import json
import time
from collections import OrderedDict
from typing import Any


class HashCache:
    """In-memory TTL cache for enrichment entity hashes.

    Caches fetched hash dicts keyed by (source_type, canonical entity_id JSON).
    Entries expire after ttl_seconds. On max_size, sweeps expired entries first,
    then evicts the oldest 10% if still over capacity.

    Uses OrderedDict for O(1) insertion-order tracking and removal (vs O(n) list).

    Not thread-safe — designed for single asyncio event loop use.
    """

    def __init__(
        self, ttl_seconds: float = 2_592_000.0, max_size: int = 50_000
    ) -> None:
        """Initialize the cache.

        Args:
            ttl_seconds: How long entries live before expiring (seconds).
            max_size: Maximum number of entries before eviction runs.
        """
        self._ttl = ttl_seconds
        self._max_size = max_size
        # OrderedDict maintains insertion order with O(1) removal
        # key -> (expiry_monotonic, hashes_dict)
        self._store: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()

    def _make_key(self, source_type: str, entity_id: dict[str, Any]) -> str:
        return f"{source_type}:{json.dumps(entity_id, sort_keys=True)}"

    def get(self, source_type: str, entity_id: dict[str, Any]) -> dict[str, Any] | None:
        """Return cached hashes for the entity, or None if missing/expired.

        Args:
            source_type: Source type (e.g., "incidentio").
            entity_id: Entity primary key fields.

        Returns:
            The cached hashes dict, or None on cache miss or expiry.
        """
        key = self._make_key(source_type, entity_id)
        entry = self._store.get(key)
        if entry is None:
            return None
        expiry, hashes = entry
        if time.monotonic() >= expiry:
            # Lazy eviction of expired entry - O(1) with OrderedDict
            del self._store[key]
            return None
        return hashes

    def put(
        self, source_type: str, entity_id: dict[str, Any], hashes: dict[str, Any]
    ) -> None:
        """Store hashes for an entity with a fresh TTL.

        If the cache is at max_size, sweeps expired entries first, then
        evicts the oldest 10% of remaining entries if still needed.

        Args:
            source_type: Source type (e.g., "incidentio").
            entity_id: Entity primary key fields.
            hashes: Hash dict to cache (keyed by DB column name).
        """
        key = self._make_key(source_type, entity_id)
        expiry = time.monotonic() + self._ttl

        if key in self._store:
            # Update in-place, keep existing order position
            self._store[key] = (expiry, hashes)
            return

        if len(self._store) >= self._max_size:
            self._evict()

        # OrderedDict maintains insertion order automatically
        self._store[key] = (expiry, hashes)

    def update(
        self, source_type: str, entity_id: dict[str, Any], new_hashes: dict[str, Any]
    ) -> None:
        """Merge new_hashes into an existing cache entry and reset TTL.

        If no entry exists, creates one with new_hashes as the full dict.

        Args:
            source_type: Source type (e.g., "incidentio").
            entity_id: Entity primary key fields.
            new_hashes: New hash values to merge in (keyed by DB column name).
        """
        key = self._make_key(source_type, entity_id)
        entry = self._store.get(key)
        if entry is not None:
            _, existing = entry
            merged = {**existing, **new_hashes}
        else:
            merged = dict(new_hashes)
        self.put(source_type, entity_id, merged)

    def invalidate(self, source_type: str, entity_id: dict[str, Any]) -> None:
        """Remove an entry from the cache if present.

        Args:
            source_type: Source type (e.g., "incidentio").
            entity_id: Entity primary key fields.
        """
        key = self._make_key(source_type, entity_id)
        # O(1) removal with OrderedDict
        self._store.pop(key, None)

    def clear(self) -> None:
        """Remove all entries from the cache."""
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)

    def _evict(self) -> None:
        """Evict expired entries, then oldest 10% if still over capacity."""
        now = time.monotonic()
        # Collect expired keys first to avoid modifying dict during iteration
        expired = [k for k, (expiry, _) in self._store.items() if now >= expiry]
        for k in expired:
            del self._store[k]

        if len(self._store) >= self._max_size:
            # Evict oldest 10% - OrderedDict iterates in insertion order
            evict_count = max(1, self._max_size // 10)
            # Get the oldest keys (first N in iteration order)
            to_evict = list(self._store.keys())[:evict_count]
            for k in to_evict:
                del self._store[k]
