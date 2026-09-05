"""Tests for list_utils."""

from dataclasses import dataclass

from common.utils.list_utils import deduplicate_by_priority


@dataclass
class Item:
    key: str
    kind: str
    value: str = ""


PRIORITY = ["A", "B", "C"]


class TestDeduplicateByPriority:
    def test_single_item_returned_as_is(self) -> None:
        items = [Item(key="x", kind="B")]
        result = deduplicate_by_priority(items, "key", "kind", PRIORITY)
        assert result == [Item(key="x", kind="B")]

    def test_no_duplicates_returns_all(self) -> None:
        items = [Item(key="x", kind="A"), Item(key="y", kind="B")]
        result = deduplicate_by_priority(items, "key", "kind", PRIORITY)
        assert len(result) == 2

    def test_higher_priority_wins(self) -> None:
        items = [Item(key="x", kind="B"), Item(key="x", kind="A")]
        result = deduplicate_by_priority(items, "key", "kind", PRIORITY)
        assert len(result) == 1
        assert result[0].kind == "A"

    def test_higher_priority_wins_regardless_of_order(self) -> None:
        items = [Item(key="x", kind="A"), Item(key="x", kind="B")]
        result = deduplicate_by_priority(items, "key", "kind", PRIORITY)
        assert len(result) == 1
        assert result[0].kind == "A"

    def test_unknown_priority_value_treated_as_lowest(self) -> None:
        items = [Item(key="x", kind="UNKNOWN"), Item(key="x", kind="C")]
        result = deduplicate_by_priority(items, "key", "kind", PRIORITY)
        assert result[0].kind == "C"

    def test_all_unknown_priority_values_first_one_wins(self) -> None:
        items = [
            Item(key="x", kind="UNKNOWN", value="first"),
            Item(key="x", kind="OTHER", value="second"),
        ]
        result = deduplicate_by_priority(items, "key", "kind", PRIORITY)
        assert len(result) == 1
        assert result[0].value == "first"

    def test_empty_list_returns_empty(self) -> None:
        result: list[Item] = deduplicate_by_priority([], "key", "kind", PRIORITY)
        assert result == []

    def test_empty_priority_list_all_treated_as_equal(self) -> None:
        items = [
            Item(key="x", kind="A", value="first"),
            Item(key="x", kind="B", value="second"),
        ]
        result = deduplicate_by_priority(items, "key", "kind", [])
        assert len(result) == 1
        assert result[0].value == "first"

    def test_preserves_first_appearance_order_of_keys(self) -> None:
        items = [
            Item(key="z", kind="A"),
            Item(key="a", kind="A"),
            Item(key="m", kind="A"),
        ]
        result = deduplicate_by_priority(items, "key", "kind", PRIORITY)
        assert [r.key for r in result] == ["z", "a", "m"]

    def test_multiple_keys_each_deduped_independently(self) -> None:
        items = [
            Item(key="x", kind="B"),
            Item(key="y", kind="C"),
            Item(key="x", kind="A"),
            Item(key="y", kind="B"),
        ]
        result = deduplicate_by_priority(items, "key", "kind", PRIORITY)
        by_key = {r.key: r.kind for r in result}
        assert by_key == {"x": "A", "y": "B"}
