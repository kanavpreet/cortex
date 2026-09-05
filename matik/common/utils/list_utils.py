"""Generic list utilities for common collection operations."""


def deduplicate_by_priority[T](
    items: list[T],
    key_field: str,
    priority_field: str,
    priority: list[str],
) -> list[T]:
    """Deduplicate a list of objects, keeping the highest-priority item per key.

    Args:
        items: Objects to deduplicate.
        key_field: Attribute name to group by (e.g. "entity_id").
        priority_field: Attribute name whose value determines which item wins
            when two items share the same key (e.g. "correlation_type").
        priority: Ordered list of values, highest priority first
            (e.g. ["SERVICE_MATCH", "LLM"]). Items whose priority_field value
            is not in this list are treated as lowest priority.

    Returns:
        List with at most one item per unique key value, preserving the
        highest-priority item for each key. Order of the returned list
        reflects the first appearance of each key.
    """

    def rank(item: T) -> int:
        val = getattr(item, priority_field)
        try:
            return priority.index(val)
        except ValueError:
            return len(priority)

    best: dict[object, T] = {}
    for item in items:
        k = getattr(item, key_field)
        if k not in best or rank(item) < rank(best[k]):
            best[k] = item
    return list(best.values())
