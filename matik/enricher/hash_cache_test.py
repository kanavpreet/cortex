"""Unit tests for HashCache."""

import time
from unittest.mock import patch

from enricher.hash_cache import HashCache


class TestHashCacheBasic:
    """Basic get/put/invalidate/clear tests."""

    def test_get_returns_none_for_missing_key(self) -> None:
        """get() returns None when no entry has been stored."""
        cache = HashCache()
        assert cache.get("incidentio", {"incident_id": "INC-1"}) is None

    def test_put_and_get_roundtrip(self) -> None:
        """put() followed by get() returns the stored hashes."""
        cache = HashCache()
        hashes = {"root_cause_summary_hash": "abc123"}
        cache.put("incidentio", {"incident_id": "INC-1"}, hashes)
        result = cache.get("incidentio", {"incident_id": "INC-1"})
        assert result == hashes

    def test_expired_entry_returns_none(self) -> None:
        """get() returns None after TTL has elapsed."""
        cache = HashCache(ttl_seconds=1.0)
        cache.put("incidentio", {"incident_id": "INC-1"}, {"h": "v"})

        # Simulate time advancing past TTL
        with patch(
            "enricher.hash_cache.time.monotonic", return_value=time.monotonic() + 2.0
        ):
            result = cache.get("incidentio", {"incident_id": "INC-1"})
        assert result is None

    def test_invalidate_removes_entry(self) -> None:
        """invalidate() removes the entry so subsequent get() returns None."""
        cache = HashCache()
        cache.put("incidentio", {"incident_id": "INC-1"}, {"h": "v"})
        cache.invalidate("incidentio", {"incident_id": "INC-1"})
        assert cache.get("incidentio", {"incident_id": "INC-1"}) is None

    def test_invalidate_nonexistent_is_safe(self) -> None:
        """invalidate() on a missing key does not raise."""
        cache = HashCache()
        cache.invalidate("incidentio", {"incident_id": "nonexistent"})

    def test_clear_empties_cache(self) -> None:
        """clear() removes all entries and len() returns 0."""
        cache = HashCache()
        cache.put("incidentio", {"incident_id": "INC-1"}, {"h": "v1"})
        cache.put("ghe_pr", {"pr_id": "42"}, {"h": "v2"})
        cache.clear()
        assert len(cache) == 0
        assert cache.get("incidentio", {"incident_id": "INC-1"}) is None

    def test_different_entity_ids_are_separate_keys(self) -> None:
        """Different entity_ids store and retrieve independently."""
        cache = HashCache()
        cache.put("incidentio", {"incident_id": "INC-1"}, {"h": "v1"})
        cache.put("incidentio", {"incident_id": "INC-2"}, {"h": "v2"})
        assert cache.get("incidentio", {"incident_id": "INC-1"}) == {"h": "v1"}
        assert cache.get("incidentio", {"incident_id": "INC-2"}) == {"h": "v2"}

    def test_different_source_types_are_separate_keys(self) -> None:
        """Same entity_id under different source_types are independent."""
        cache = HashCache()
        cache.put("incidentio", {"id": "1"}, {"h": "a"})
        cache.put("jira", {"id": "1"}, {"h": "b"})
        assert cache.get("incidentio", {"id": "1"}) == {"h": "a"}
        assert cache.get("jira", {"id": "1"}) == {"h": "b"}

    def test_len_reflects_stored_entries(self) -> None:
        """__len__ returns the number of stored (non-expired) entries."""
        cache = HashCache()
        assert len(cache) == 0
        cache.put("incidentio", {"incident_id": "INC-1"}, {})
        assert len(cache) == 1
        cache.put("incidentio", {"incident_id": "INC-2"}, {})
        assert len(cache) == 2


class TestHashCacheUpdate:
    """Tests for the update() method."""

    def test_update_merges_hashes_and_resets_ttl(self) -> None:
        """update() merges new hashes into existing entry and resets the TTL."""
        cache = HashCache(ttl_seconds=60.0)
        cache.put("incidentio", {"incident_id": "INC-1"}, {"h1": "old"})

        # Advance time but still within TTL
        future = time.monotonic() + 30.0
        with patch("enricher.hash_cache.time.monotonic", return_value=future):
            cache.update("incidentio", {"incident_id": "INC-1"}, {"h2": "new"})
            result = cache.get("incidentio", {"incident_id": "INC-1"})

        assert result == {"h1": "old", "h2": "new"}

    def test_update_overwrites_existing_key(self) -> None:
        """update() overwrites an existing hash key with the new value."""
        cache = HashCache()
        cache.put("incidentio", {"incident_id": "INC-1"}, {"h": "old"})
        cache.update("incidentio", {"incident_id": "INC-1"}, {"h": "new"})
        assert cache.get("incidentio", {"incident_id": "INC-1"}) == {"h": "new"}

    def test_update_creates_entry_when_missing(self) -> None:
        """update() creates a new entry when none exists."""
        cache = HashCache()
        cache.update("incidentio", {"incident_id": "INC-1"}, {"h": "val"})
        assert cache.get("incidentio", {"incident_id": "INC-1"}) == {"h": "val"}


class TestHashCacheEviction:
    """Tests for max_size eviction behavior."""

    def test_max_size_evicts_expired_first(self) -> None:
        """When at max_size, eviction removes expired entries first."""
        now = time.monotonic()
        cache = HashCache(ttl_seconds=60.0, max_size=3)

        # Fill cache to max_size
        cache.put("t", {"id": "1"}, {"h": "v1"})
        cache.put("t", {"id": "2"}, {"h": "v2"})
        cache.put("t", {"id": "3"}, {"h": "v3"})

        # Expire entry "1" and "2" by rewinding their expiry
        cache._store['t:{"id": "1"}'] = (now - 1, {"h": "v1"})
        cache._store['t:{"id": "2"}'] = (now - 1, {"h": "v2"})

        # Adding a 4th entry should trigger eviction of the expired entries
        cache.put("t", {"id": "4"}, {"h": "v4"})

        # Entry "3" and "4" survive; expired entries are gone
        assert cache.get("t", {"id": "3"}) == {"h": "v3"}
        assert cache.get("t", {"id": "4"}) == {"h": "v4"}
        assert len(cache) == 2

    def test_max_size_evicts_oldest_when_no_expired(self) -> None:
        """When at max_size and no expired entries, oldest 10% are evicted."""
        cache = HashCache(ttl_seconds=3600.0, max_size=10)

        # Fill to capacity
        for i in range(10):
            cache.put("t", {"id": str(i)}, {"h": str(i)})

        # Adding one more triggers eviction of oldest 10% = 1 entry (entry "0")
        cache.put("t", {"id": "10"}, {"h": "10"})

        # Oldest entry evicted
        assert cache.get("t", {"id": "0"}) is None
        # Newer entries survive
        assert cache.get("t", {"id": "1"}) is not None
        assert cache.get("t", {"id": "10"}) is not None
