"""Unit tests for JIRA utilities."""

from common.utils.jira_utils import (
    COMMENT_DELIMITER,
    CUSTOM_FIELDS_TCMR_RELATED_GIT_PR_LINK,
    CUSTOM_FIELDS_TCMR_RELATED_SERVICES,
    aggregate_comments_for_llm,
    extract_tcmr_key,
    extract_tcmr_link,
    get_first_non_empty_string_field,
    get_string_list_field,
)


class TestExtractTcmrLink:
    """Test suite for extract_tcmr_link function."""

    def test_extracts_tcmr_link(self) -> None:
        """Test extracting TCMR link from text."""
        text = "This PR fixes TCMR-12345. See https://jira.airbnb.biz/browse/TCMR-12345 for details"
        assert extract_tcmr_link(text) == "https://jira.airbnb.biz/browse/TCMR-12345"

    def test_extracts_first_tcmr_link(self) -> None:
        """Test extracts first TCMR link when multiple present."""
        text = "https://jira.airbnb.biz/browse/TCMR-111 and https://jira.airbnb.biz/browse/TCMR-222"
        assert extract_tcmr_link(text) == "https://jira.airbnb.biz/browse/TCMR-111"

    def test_returns_empty_when_no_link(self) -> None:
        """Test returns empty string when no TCMR link found."""
        assert extract_tcmr_link("No TCMR link here") == ""
        assert extract_tcmr_link("") == ""

    def test_does_not_match_partial_links(self) -> None:
        """Test doesn't match malformed links."""
        assert extract_tcmr_link("https://jira.airbnb.biz/browse/TCMR-") == ""
        assert extract_tcmr_link("jira.airbnb.biz/browse/TCMR-123") == ""

    def test_matches_various_tcmr_numbers(self) -> None:
        """Test matches TCMR links with various number lengths."""
        assert (
            extract_tcmr_link("https://jira.airbnb.biz/browse/TCMR-1")
            == "https://jira.airbnb.biz/browse/TCMR-1"
        )
        assert (
            extract_tcmr_link("https://jira.airbnb.biz/browse/TCMR-123456")
            == "https://jira.airbnb.biz/browse/TCMR-123456"
        )


class TestExtractTcmrKey:
    """Test suite for extract_tcmr_key function."""

    def test_extracts_tcmr_key(self) -> None:
        """Test extracting the TCMR issue key from a browse URL in text."""
        text = "See https://jira.airbnb.biz/browse/TCMR-12345 for details"
        assert extract_tcmr_key(text) == "TCMR-12345"

    def test_extracts_first_tcmr_key(self) -> None:
        """Test extracts key of the first TCMR link when multiple present."""
        text = "https://jira.airbnb.biz/browse/TCMR-111 and https://jira.airbnb.biz/browse/TCMR-222"
        assert extract_tcmr_key(text) == "TCMR-111"

    def test_returns_empty_when_no_link(self) -> None:
        """Test returns empty string when no TCMR browse URL is present."""
        assert extract_tcmr_key("No TCMR link here") == ""
        assert extract_tcmr_key("") == ""

    def test_ignores_bare_key_mention(self) -> None:
        """Test a bare 'TCMR-123' mention (no browse URL) is not matched."""
        assert extract_tcmr_key("This PR relates to TCMR-123 but has no link") == ""
        assert extract_tcmr_key("https://jira.airbnb.biz/browse/TCMR-") == ""

    def test_matches_various_tcmr_numbers(self) -> None:
        """Test matches TCMR keys with various number lengths."""
        assert extract_tcmr_key("https://jira.airbnb.biz/browse/TCMR-1") == "TCMR-1"
        assert (
            extract_tcmr_key("https://jira.airbnb.biz/browse/TCMR-123456")
            == "TCMR-123456"
        )


class TestAggregateCommentsForLlm:
    """Test suite for aggregate_comments_for_llm function."""

    def test_aggregates_comments(self) -> None:
        """Test aggregating multiple comments."""
        comments = ["First comment", "Second comment", "Third comment"]
        result = aggregate_comments_for_llm(comments)
        assert result == "First comment ;; Second comment ;; Third comment"

    def test_single_comment(self) -> None:
        """Test single comment returns without delimiter."""
        assert aggregate_comments_for_llm(["Only comment"]) == "Only comment"

    def test_empty_list_returns_empty(self) -> None:
        """Test empty list returns empty string."""
        assert aggregate_comments_for_llm([]) == ""

    def test_none_returns_empty(self) -> None:
        """Test None returns empty string."""
        assert aggregate_comments_for_llm(None) == ""

    def test_filters_empty_comments(self) -> None:
        """Test filters out empty and whitespace-only comments."""
        comments = ["Valid", "", "  ", "Also valid", None]
        result = aggregate_comments_for_llm(comments)
        assert result == "Valid ;; Also valid"

    def test_strips_whitespace(self) -> None:
        """Test strips whitespace from comments."""
        comments = ["  First  ", "\nSecond\n", "Third"]
        result = aggregate_comments_for_llm(comments)
        assert result == "First ;; Second ;; Third"

    def test_all_empty_returns_empty(self) -> None:
        """Test all empty comments returns empty string."""
        assert aggregate_comments_for_llm(["", "  ", None]) == ""


class TestGetFirstNonEmptyStringField:
    """Test suite for get_first_non_empty_string_field function."""

    def test_returns_first_non_empty(self) -> None:
        """Test returns first non-empty string value."""
        fields = {"key1": "", "key2": "value2", "key3": "value3"}
        keys = ["key1", "key2", "key3"]
        assert get_first_non_empty_string_field(fields, keys) == "value2"

    def test_returns_first_key_if_non_empty(self) -> None:
        """Test returns first key's value if non-empty."""
        fields = {"key1": "value1", "key2": "value2"}
        keys = ["key1", "key2"]
        assert get_first_non_empty_string_field(fields, keys) == "value1"

    def test_returns_empty_when_none_found(self) -> None:
        """Test returns empty string when no non-empty value found."""
        fields = {"key1": "", "key2": ""}
        keys = ["key1", "key2"]
        assert get_first_non_empty_string_field(fields, keys) == ""

    def test_returns_empty_when_keys_missing(self) -> None:
        """Test returns empty string when keys don't exist."""
        fields = {"other": "value"}
        keys = ["key1", "key2"]
        assert get_first_non_empty_string_field(fields, keys) == ""

    def test_returns_empty_when_fields_none(self) -> None:
        """Test returns empty string when fields is None."""
        assert get_first_non_empty_string_field(None, ["key1"]) == ""

    def test_skips_non_string_values(self) -> None:
        """Test skips non-string values."""
        fields = {"key1": 123, "key2": ["list"], "key3": "valid"}
        keys = ["key1", "key2", "key3"]
        assert get_first_non_empty_string_field(fields, keys) == "valid"

    def test_empty_keys_returns_empty(self) -> None:
        """Test empty keys list returns empty string."""
        fields = {"key1": "value1"}
        assert get_first_non_empty_string_field(fields, []) == ""


class TestGetStringListField:
    """Test suite for get_string_list_field function."""

    def test_returns_none_when_fields_none(self) -> None:
        """Test returns None when fields is None."""
        assert get_string_list_field(None, ["key1"]) is None

    def test_returns_none_when_no_keys_match(self) -> None:
        """Test returns None when no keys exist in fields."""
        fields = {"other": "value"}
        assert get_string_list_field(fields, ["key1", "key2"]) is None

    def test_returns_none_when_value_is_none(self) -> None:
        """Test skips keys with None values."""
        fields = {"key1": None, "key2": None}
        assert get_string_list_field(fields, ["key1", "key2"]) is None

    def test_string_value_returns_single_item_list(self) -> None:
        """Test string value is wrapped in a list."""
        fields = {"key1": "service-abc"}
        assert get_string_list_field(fields, ["key1"]) == ["service-abc"]

    def test_string_value_is_stripped(self) -> None:
        """Test string value whitespace is stripped."""
        fields = {"key1": "  service-abc  "}
        assert get_string_list_field(fields, ["key1"]) == ["service-abc"]

    def test_empty_string_skipped(self) -> None:
        """Test empty string values are skipped."""
        fields = {"key1": "   ", "key2": "valid"}
        assert get_string_list_field(fields, ["key1", "key2"]) == ["valid"]

    def test_list_of_strings(self) -> None:
        """Test list of strings returned as-is."""
        fields = {"key1": ["service-a", "service-b"]}
        assert get_string_list_field(fields, ["key1"]) == ["service-a", "service-b"]

    def test_list_of_strings_strips_whitespace(self) -> None:
        """Test list of strings are stripped."""
        fields = {"key1": ["  service-a  ", "service-b"]}
        assert get_string_list_field(fields, ["key1"]) == ["service-a", "service-b"]

    def test_list_filters_empty_strings(self) -> None:
        """Test list filters out empty and whitespace-only strings."""
        fields = {"key1": ["valid", "", "  ", "also-valid"]}
        assert get_string_list_field(fields, ["key1"]) == ["valid", "also-valid"]

    def test_list_of_dicts_with_value_key(self) -> None:
        """Test list of dicts extracts 'value' key."""
        fields = {"key1": [{"value": "svc-a"}, {"value": "svc-b"}]}
        assert get_string_list_field(fields, ["key1"]) == ["svc-a", "svc-b"]

    def test_list_of_dicts_with_name_key(self) -> None:
        """Test list of dicts falls back to 'name' key."""
        fields = {"key1": [{"name": "svc-a"}]}
        assert get_string_list_field(fields, ["key1"]) == ["svc-a"]

    def test_list_of_dicts_with_id_key(self) -> None:
        """Test list of dicts falls back to 'id' key."""
        fields = {"key1": [{"id": "svc-a"}]}
        assert get_string_list_field(fields, ["key1"]) == ["svc-a"]

    def test_list_of_dicts_prefers_value_over_name(self) -> None:
        """Test 'value' key is preferred over 'name'."""
        fields = {"key1": [{"value": "from-value", "name": "from-name"}]}
        assert get_string_list_field(fields, ["key1"]) == ["from-value"]

    def test_empty_list_returns_none(self) -> None:
        """Test empty list skips to next key or returns None."""
        fields: dict[str, list[str]] = {"key1": []}
        assert get_string_list_field(fields, ["key1"]) is None

    def test_list_of_empty_dicts_returns_none(self) -> None:
        """Test list of dicts with no valid keys returns None."""
        fields = {"key1": [{"other": "val"}]}
        assert get_string_list_field(fields, ["key1"]) is None

    def test_checks_keys_in_order(self) -> None:
        """Test returns value from first matching key."""
        fields = {"key1": ["first"], "key2": ["second"]}
        assert get_string_list_field(fields, ["key1", "key2"]) == ["first"]

    def test_skips_none_to_next_key(self) -> None:
        """Test skips None value and checks next key."""
        fields = {"key1": None, "key2": ["found"]}
        assert get_string_list_field(fields, ["key1", "key2"]) == ["found"]


class TestConstants:
    """Test suite for JIRA constants."""

    def test_custom_fields_git_pr_link_not_empty(self) -> None:
        """Test TCMR Git PR link custom fields list is not empty."""
        assert len(CUSTOM_FIELDS_TCMR_RELATED_GIT_PR_LINK) > 0
        assert all(
            f.startswith("customfield_") for f in CUSTOM_FIELDS_TCMR_RELATED_GIT_PR_LINK
        )

    def test_custom_fields_service_id_not_empty(self) -> None:
        """Test TCMR service ID custom fields list is not empty."""
        assert len(CUSTOM_FIELDS_TCMR_RELATED_SERVICES) > 0
        assert all(
            f.startswith("customfield_") for f in CUSTOM_FIELDS_TCMR_RELATED_SERVICES
        )

    def test_comment_delimiter(self) -> None:
        """Test comment delimiter is defined."""
        assert COMMENT_DELIMITER == " ;; "
