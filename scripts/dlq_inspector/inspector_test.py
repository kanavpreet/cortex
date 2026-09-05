"""Unit tests for dlq_inspector.inspector."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from .inspector import (
    _LONG_POLL_WAIT_SECONDS,
    _MAX_CONSECUTIVE_EMPTY_POLLS,
    ACCOUNT_ID,
    DEFAULT_MAX_MESSAGES,
    DLQ_CATALOGUE,
    REGION,
    _analyse_messages,
    _ask_action,
    _ask_dry_run,
    _ask_env,
    _ask_max_messages,
    _ask_queues,
    _ask_redrive_filters,
    _ask_redrive_limit,
    _ask_redrive_mode,
    _ask_throttle,
    _bold,
    _confirm_production,
    _generate_report,
    _get_queue_depth,
    _match_filter,
    _peek_dlq,
    _queue_arn,
    _queue_name,
    _redrive_manual,
    _redrive_native,
    _save_messages,
    _source_queue_url,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_msg(
    msg_id: str = "msg-1",
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if body is None:
        body = {"source_type": "ghe_pr", "message_type": "enrichment"}
    return {
        "MessageId": msg_id,
        "Body": json.dumps(body),
        "ReceiptHandle": f"receipt-{msg_id}",
    }


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_default_max_messages(self) -> None:
        assert DEFAULT_MAX_MESSAGES == 50

    def test_account_id_non_empty(self) -> None:
        assert ACCOUNT_ID != ""

    def test_region_non_empty(self) -> None:
        assert REGION != ""

    def test_dlq_catalogue_has_three_envs(self) -> None:
        assert set(DLQ_CATALOGUE.keys()) == {"sandbox", "staging", "production"}

    def test_all_urls_contain_account_id(self) -> None:
        for env, queues in DLQ_CATALOGUE.items():
            for label, url in queues:
                assert ACCOUNT_ID in url, f"{env}/{label} URL missing account id"

    def test_all_urls_contain_region(self) -> None:
        for env, queues in DLQ_CATALOGUE.items():
            for label, url in queues:
                assert REGION in url, f"{env}/{label} URL missing region"

    def test_all_urls_end_in_dlq(self) -> None:
        for env, queues in DLQ_CATALOGUE.items():
            for label, url in queues:
                assert url.endswith("-dlq"), f"{env}/{label} URL does not end in -dlq"

    def test_labels_are_non_empty_strings(self) -> None:
        for env, queues in DLQ_CATALOGUE.items():
            for label, _ in queues:
                assert isinstance(label, str) and label, f"{env} has empty label"


# ---------------------------------------------------------------------------
# _queue_name
# ---------------------------------------------------------------------------


class TestQueueName:
    def test_standard_url(self) -> None:
        url = "https://sqs.us-east-1.amazonaws.com/123456789/my-queue-dlq"
        assert _queue_name(url) == "my-queue-dlq"

    def test_url_with_trailing_slash(self) -> None:
        url = "https://sqs.us-east-1.amazonaws.com/123456789/my-queue-dlq/"
        assert _queue_name(url) == "my-queue-dlq"

    def test_real_catalogue_url(self) -> None:
        _, url = DLQ_CATALOGUE["sandbox"][0]
        assert _queue_name(url) == "matik-scrb-high-sandbox-dlq"


# ---------------------------------------------------------------------------
# _bold
# ---------------------------------------------------------------------------


class TestBold:
    def test_wraps_with_ansi_codes(self) -> None:
        result = _bold("hello")
        assert "hello" in result
        assert result != "hello"

    def test_empty_string(self) -> None:
        result = _bold("")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _ask_env
# ---------------------------------------------------------------------------


class TestAskEnv:
    def test_accepts_env_name(self) -> None:
        with patch("builtins.input", return_value="sandbox"):
            assert _ask_env() == "sandbox"

    def test_accepts_numeric_choice(self) -> None:
        envs = list(DLQ_CATALOGUE.keys())
        with patch("builtins.input", return_value="1"):
            assert _ask_env() == envs[0]

    def test_retries_on_invalid_then_accepts(self) -> None:
        with patch("builtins.input", side_effect=["invalid", "staging"]):
            assert _ask_env() == "staging"

    def test_accepts_last_env_by_number(self) -> None:
        envs = list(DLQ_CATALOGUE.keys())
        with patch("builtins.input", return_value=str(len(envs))):
            assert _ask_env() == envs[-1]


# ---------------------------------------------------------------------------
# _ask_queues
# ---------------------------------------------------------------------------


class TestAskQueues:
    def test_all_keyword_returns_all_queues(self) -> None:
        with patch("builtins.input", return_value="all"):
            result = _ask_queues("sandbox")
        assert result == DLQ_CATALOGUE["sandbox"]

    def test_all_by_number_returns_all_queues(self) -> None:
        n = len(DLQ_CATALOGUE["sandbox"]) + 1
        with patch("builtins.input", return_value=str(n)):
            result = _ask_queues("sandbox")
        assert result == DLQ_CATALOGUE["sandbox"]

    def test_single_numeric_choice(self) -> None:
        with patch("builtins.input", return_value="1"):
            result = _ask_queues("sandbox")
        assert result == [DLQ_CATALOGUE["sandbox"][0]]

    def test_comma_separated_choices(self) -> None:
        with patch("builtins.input", return_value="1,3"):
            result = _ask_queues("sandbox")
        assert result == [DLQ_CATALOGUE["sandbox"][0], DLQ_CATALOGUE["sandbox"][2]]

    def test_retries_on_out_of_range_then_accepts(self) -> None:
        with patch("builtins.input", side_effect=["99", "2"]):
            result = _ask_queues("sandbox")
        assert result == [DLQ_CATALOGUE["sandbox"][1]]

    def test_retries_on_non_numeric_then_accepts(self) -> None:
        with patch("builtins.input", side_effect=["abc", "1"]):
            result = _ask_queues("sandbox")
        assert result == [DLQ_CATALOGUE["sandbox"][0]]


# ---------------------------------------------------------------------------
# _ask_max_messages
# ---------------------------------------------------------------------------


class TestAskMaxMessages:
    def test_empty_input_returns_default(self) -> None:
        with patch("builtins.input", return_value=""):
            assert _ask_max_messages() == DEFAULT_MAX_MESSAGES

    def test_valid_number_returned(self) -> None:
        with patch("builtins.input", return_value="100"):
            assert _ask_max_messages() == 100

    def test_non_numeric_falls_back_to_default(self) -> None:
        with patch("builtins.input", return_value="abc"):
            assert _ask_max_messages() == DEFAULT_MAX_MESSAGES

    def test_zero_falls_back_to_default(self) -> None:
        with patch("builtins.input", return_value="0"):
            assert _ask_max_messages() == DEFAULT_MAX_MESSAGES

    def test_negative_falls_back_to_default(self) -> None:
        with patch("builtins.input", return_value="-5"):
            assert _ask_max_messages() == DEFAULT_MAX_MESSAGES

    def test_all_returns_none(self) -> None:
        with patch("builtins.input", return_value="all"):
            assert _ask_max_messages() is None

    def test_all_case_insensitive(self) -> None:
        with patch("builtins.input", return_value="ALL"):
            assert _ask_max_messages() is None


# ---------------------------------------------------------------------------
# _confirm_production
# ---------------------------------------------------------------------------


class TestConfirmProduction:
    def test_non_production_returns_true_without_prompt(self) -> None:
        assert _confirm_production("sandbox") is True
        assert _confirm_production("staging") is True

    def test_production_yes_returns_true(self) -> None:
        with patch("builtins.input", return_value="yes"):
            assert _confirm_production("production") is True

    def test_production_no_returns_false(self) -> None:
        with patch("builtins.input", return_value="no"):
            assert _confirm_production("production") is False

    def test_production_empty_returns_false(self) -> None:
        with patch("builtins.input", return_value=""):
            assert _confirm_production("production") is False


# ---------------------------------------------------------------------------
# _analyse_messages
# ---------------------------------------------------------------------------


class TestAnalyseMessages:
    def test_empty_list(self) -> None:
        result = _analyse_messages([])
        assert result["total"] == 0
        assert result["source_types"] == {}
        assert result["message_types"] == {}
        assert result["samples"] == []

    def test_single_message_counted(self) -> None:
        msg = _make_msg(body={"source_type": "ghe_pr", "message_type": "enrichment"})
        result = _analyse_messages([msg])
        assert result["total"] == 1
        assert result["source_types"] == {"ghe_pr": 1}
        assert result["message_types"] == {"enrichment": 1}

    def test_multiple_source_types_counted(self) -> None:
        msgs = [
            _make_msg("m1", {"source_type": "ghe_pr", "message_type": "enrichment"}),
            _make_msg("m2", {"source_type": "jira", "message_type": "enrichment"}),
            _make_msg("m3", {"source_type": "ghe_pr", "message_type": "write"}),
        ]
        result = _analyse_messages(msgs)
        assert result["source_types"] == {"ghe_pr": 2, "jira": 1}
        assert result["message_types"] == {"enrichment": 2, "write": 1}

    def test_unknown_source_type_when_field_missing(self) -> None:
        msg = _make_msg(body={"message_type": "enrichment"})
        result = _analyse_messages([msg])
        assert result["source_types"] == {"unknown": 1}

    def test_unknown_message_type_when_field_missing(self) -> None:
        msg = _make_msg(body={"source_type": "ghe_pr"})
        result = _analyse_messages([msg])
        assert result["message_types"] == {"unknown": 1}

    def test_invalid_json_body_counted_as_unknown(self) -> None:
        msg = {"MessageId": "m1", "Body": "not valid json"}
        result = _analyse_messages([msg])
        assert result["total"] == 1
        assert result["source_types"] == {"unknown": 1}

    def test_non_dict_json_body_counted_as_unknown(self) -> None:
        msg = {"MessageId": "m1", "Body": json.dumps([1, 2, 3])}
        result = _analyse_messages([msg])
        assert result["source_types"] == {"unknown": 1}

    def test_up_to_three_samples_collected(self) -> None:
        msgs = [_make_msg(f"m{i}") for i in range(5)]
        result = _analyse_messages(msgs)
        assert len(result["samples"]) == 3

    def test_body_truncated_at_800_chars(self) -> None:
        large_body = {
            "source_type": "ghe_pr",
            "message_type": "write",
            "data": "x" * 1000,
        }
        msg = _make_msg(body=large_body)
        result = _analyse_messages([msg])
        assert result["samples"][0].endswith("... (truncated)")

    def test_body_not_truncated_when_short(self) -> None:
        msg = _make_msg(body={"source_type": "ghe_pr", "message_type": "write"})
        result = _analyse_messages([msg])
        assert not result["samples"][0].endswith("... (truncated)")


# ---------------------------------------------------------------------------
# _save_messages
# ---------------------------------------------------------------------------


class TestSaveMessages:
    def test_creates_output_directory(self, tmp_path: Path) -> None:
        msg = _make_msg()
        _save_messages(
            [msg],
            "sandbox",
            "https://sqs.aws.com/123/my-dlq",
            "20240101_120000",
            tmp_path,
        )
        assert (tmp_path / "sandbox" / "my-dlq").is_dir()

    def test_creates_one_file_per_message(self, tmp_path: Path) -> None:
        msgs = [_make_msg("id1"), _make_msg("id2")]
        _save_messages(
            msgs,
            "sandbox",
            "https://sqs.aws.com/123/my-dlq",
            "20240101_120000",
            tmp_path,
        )
        out_dir = tmp_path / "sandbox" / "my-dlq"
        assert len(list(out_dir.glob("*.json"))) == 2

    def test_filename_contains_run_ts_and_message_id(self, tmp_path: Path) -> None:
        msg = _make_msg("abc-123")
        _save_messages(
            [msg],
            "sandbox",
            "https://sqs.aws.com/123/my-dlq",
            "20240101_120000",
            tmp_path,
        )
        out_dir = tmp_path / "sandbox" / "my-dlq"
        files = list(out_dir.glob("*.json"))
        assert any("20240101_120000" in f.name and "abc-123" in f.name for f in files)

    def test_file_contains_parsed_body(self, tmp_path: Path) -> None:
        body = {"source_type": "ghe_pr", "message_type": "enrichment"}
        msg = _make_msg(body=body)
        _save_messages(
            [msg], "sandbox", "https://sqs.aws.com/123/my-dlq", "ts", tmp_path
        )
        out_dir = tmp_path / "sandbox" / "my-dlq"
        saved = json.loads(next(out_dir.glob("*.json")).read_text())
        assert saved["parsed_body"] == body

    def test_invalid_json_body_stores_none_as_parsed_body(self, tmp_path: Path) -> None:
        msg = {"MessageId": "m1", "Body": "not-json", "ReceiptHandle": "r1"}
        _save_messages(
            [msg], "sandbox", "https://sqs.aws.com/123/my-dlq", "ts", tmp_path
        )
        out_dir = tmp_path / "sandbox" / "my-dlq"
        saved = json.loads(next(out_dir.glob("*.json")).read_text())
        assert saved["parsed_body"] is None

    def test_returns_output_directory_path(self, tmp_path: Path) -> None:
        msg = _make_msg()
        result = _save_messages(
            [msg], "staging", "https://sqs.aws.com/123/my-dlq", "ts", tmp_path
        )
        assert result == tmp_path / "staging" / "my-dlq"

    def test_empty_messages_creates_directory_but_no_files(
        self, tmp_path: Path
    ) -> None:
        _save_messages([], "sandbox", "https://sqs.aws.com/123/my-dlq", "ts", tmp_path)
        out_dir = tmp_path / "sandbox" / "my-dlq"
        assert out_dir.is_dir()
        assert len(list(out_dir.glob("*.json"))) == 0


# ---------------------------------------------------------------------------
# _generate_report
# ---------------------------------------------------------------------------


class TestGenerateReport:
    def _make_results(
        self, messages_per_queue: list[list[dict[str, Any]]] | None = None
    ) -> list[tuple[str, str, int, list[dict[str, Any]]]]:
        if messages_per_queue is None:
            messages_per_queue = [[]]
        results = []
        for i, msgs in enumerate(messages_per_queue):
            label = f"Queue {i + 1}"
            url = f"https://sqs.aws.com/123/queue-{i + 1}-dlq"
            results.append((label, url, len(msgs), msgs))
        return results

    def test_creates_report_file(self, tmp_path: Path) -> None:
        results = self._make_results()
        path = _generate_report("sandbox", results, "20240101_120000", tmp_path, 50)
        assert path.exists()

    def test_report_path_format(self, tmp_path: Path) -> None:
        results = self._make_results()
        path = _generate_report("sandbox", results, "20240101_120000", tmp_path, 50)
        assert path == tmp_path / "sandbox" / "report_20240101_120000.md"

    def test_report_contains_env_heading(self, tmp_path: Path) -> None:
        results = self._make_results()
        path = _generate_report("staging", results, "ts", tmp_path, 50)
        content = path.read_text()
        assert "staging" in content

    def test_report_contains_queue_labels(self, tmp_path: Path) -> None:
        results = self._make_results([[_make_msg()]])
        path = _generate_report("sandbox", results, "ts", tmp_path, 50)
        content = path.read_text()
        assert "Queue 1" in content

    def test_report_shows_source_type_breakdown(self, tmp_path: Path) -> None:
        msgs = [
            _make_msg("m1", {"source_type": "ghe_pr", "message_type": "enrichment"}),
            _make_msg("m2", {"source_type": "jira", "message_type": "enrichment"}),
        ]
        results = self._make_results([msgs])
        path = _generate_report("sandbox", results, "ts", tmp_path, 50)
        content = path.read_text()
        assert "ghe_pr" in content
        assert "jira" in content

    def test_report_shows_no_messages_for_empty_queue(self, tmp_path: Path) -> None:
        results = self._make_results([[]])
        path = _generate_report("sandbox", results, "ts", tmp_path, 50)
        content = path.read_text()
        assert "No messages" in content

    def test_report_includes_max_messages(self, tmp_path: Path) -> None:
        results = self._make_results()
        path = _generate_report("sandbox", results, "ts", tmp_path, 25)
        content = path.read_text()
        assert "25" in content

    def test_report_shows_no_cap_when_max_messages_none(self, tmp_path: Path) -> None:
        results = self._make_results()
        path = _generate_report("sandbox", results, "ts", tmp_path, None)
        content = path.read_text()
        assert "all (no cap)" in content

    def test_report_has_summary_table(self, tmp_path: Path) -> None:
        results = self._make_results([[_make_msg()]])
        path = _generate_report("sandbox", results, "ts", tmp_path, 50)
        content = path.read_text()
        assert "## Summary" in content

    def test_report_shows_negative_depth_as_unknown(self, tmp_path: Path) -> None:
        results: list[tuple[str, str, int, list[dict[str, Any]]]] = [
            ("Queue 1", "https://sqs.aws.com/123/q-dlq", -1, [])
        ]
        path = _generate_report("sandbox", results, "ts", tmp_path, 50)
        content = path.read_text()
        assert "unknown" in content

    def test_report_multiple_queues(self, tmp_path: Path) -> None:
        results = self._make_results([[_make_msg("a")], [_make_msg("b")], []])
        path = _generate_report("production", results, "ts", tmp_path, 50)
        content = path.read_text()
        assert "Queue 1" in content
        assert "Queue 2" in content
        assert "Queue 3" in content


# ---------------------------------------------------------------------------
# _peek_dlq
# ---------------------------------------------------------------------------


class TestPeekDlq:
    def _make_sqs(self, batches: list[list[dict[str, Any]]]) -> MagicMock:
        """
        Return a mock SQS client whose receive_message returns successive batches,
        then empty responses forever after (enough to satisfy the consecutive-empty-
        poll drain check).
        """
        sqs = MagicMock()
        responses = [{"Messages": b} for b in batches] + [
            {"Messages": []} for _ in range(_MAX_CONSECUTIVE_EMPTY_POLLS + 1)
        ]
        sqs.receive_message.side_effect = responses
        return sqs

    def test_returns_empty_when_queue_empty(self) -> None:
        sqs = self._make_sqs([[]])
        result = _peek_dlq(sqs, "https://sqs.aws.com/123/q-dlq", 10)
        assert result == []

    def test_returns_messages_from_single_batch(self) -> None:
        msgs = [_make_msg("m1"), _make_msg("m2")]
        sqs = self._make_sqs([msgs])
        result = _peek_dlq(sqs, "https://sqs.aws.com/123/q-dlq", 10)
        assert len(result) == 2

    def test_deduplicates_by_message_id(self) -> None:
        msg = _make_msg("dup")
        sqs = self._make_sqs([[msg], [msg]])
        result = _peek_dlq(sqs, "https://sqs.aws.com/123/q-dlq", 20)
        assert len(result) == 1

    def test_respects_max_messages_limit(self) -> None:
        batch = [_make_msg(f"m{i}") for i in range(10)]
        sqs = MagicMock()

        # Real SQS honours MaxNumberOfMessages — return only what was requested
        def _receive(**kwargs: Any) -> dict[str, Any]:
            n = kwargs.get("MaxNumberOfMessages", 10)
            return {"Messages": batch[:n]}

        sqs.receive_message.side_effect = _receive
        result = _peek_dlq(sqs, "https://sqs.aws.com/123/q-dlq", 5)
        assert len(result) == 5

    def test_uses_visibility_timeout_zero(self) -> None:
        sqs = self._make_sqs([[_make_msg()]])
        _peek_dlq(sqs, "https://sqs.aws.com/123/q-dlq", 10)
        call_kwargs = sqs.receive_message.call_args[1]
        assert call_kwargs.get("VisibilityTimeout") == 0

    def test_uses_long_polling(self) -> None:
        sqs = self._make_sqs([[_make_msg()]])
        _peek_dlq(sqs, "https://sqs.aws.com/123/q-dlq", 10)
        call_kwargs = sqs.receive_message.call_args[1]
        assert call_kwargs.get("WaitTimeSeconds") == _LONG_POLL_WAIT_SECONDS

    def test_stops_after_consecutive_empty_polls(self) -> None:
        sqs = self._make_sqs([[_make_msg("m1")], []])
        result = _peek_dlq(sqs, "https://sqs.aws.com/123/q-dlq", 50)
        assert len(result) == 1

    def test_collects_across_multiple_batches(self) -> None:
        batch1 = [_make_msg(f"m{i}") for i in range(10)]
        batch2 = [_make_msg(f"n{i}") for i in range(5)]
        sqs = self._make_sqs([batch1, batch2])
        result = _peek_dlq(sqs, "https://sqs.aws.com/123/q-dlq", 50)
        assert len(result) == 15

    def test_does_not_stop_on_single_short_batch(self) -> None:
        """
        A single short/partial batch does NOT mean the queue is drained — SQS
        can legitimately return fewer messages than requested even when more
        remain. This is the bug that caused a 53-message queue to only yield 25.
        """
        batch1 = [_make_msg(f"m{i}") for i in range(3)]  # short batch, not empty
        batch2 = [_make_msg(f"n{i}") for i in range(10)]
        sqs = self._make_sqs([batch1, batch2])
        result = _peek_dlq(sqs, "https://sqs.aws.com/123/q-dlq", 50)
        assert len(result) == 13

    def test_all_no_cap_retrieves_every_message(self) -> None:
        batch1 = [_make_msg(f"m{i}") for i in range(10)]
        batch2 = [_make_msg(f"n{i}") for i in range(10)]
        batch3 = [_make_msg(f"o{i}") for i in range(3)]
        sqs = self._make_sqs([batch1, batch2, batch3])
        result = _peek_dlq(sqs, "https://sqs.aws.com/123/q-dlq", None)
        assert len(result) == 23

    def test_all_no_cap_requests_batch_size_ten(self) -> None:
        sqs = self._make_sqs([[_make_msg()]])
        _peek_dlq(sqs, "https://sqs.aws.com/123/q-dlq", None)
        call_kwargs = sqs.receive_message.call_args_list[0][1]
        assert call_kwargs["MaxNumberOfMessages"] == 10


# ---------------------------------------------------------------------------
# _get_queue_depth
# ---------------------------------------------------------------------------


class TestGetQueueDepth:
    def test_returns_depth_from_attributes(self) -> None:
        sqs = MagicMock()
        sqs.get_queue_attributes.return_value = {
            "Attributes": {"ApproximateNumberOfMessages": "42"}
        }
        assert _get_queue_depth(sqs, "https://sqs.aws.com/123/q-dlq") == 42

    def test_returns_zero_when_attribute_missing(self) -> None:
        sqs = MagicMock()
        sqs.get_queue_attributes.return_value = {"Attributes": {}}
        assert _get_queue_depth(sqs, "https://sqs.aws.com/123/q-dlq") == 0

    def test_returns_minus_one_on_exception(self) -> None:
        sqs = MagicMock()
        sqs.get_queue_attributes.side_effect = Exception("access denied")
        assert _get_queue_depth(sqs, "https://sqs.aws.com/123/q-dlq") == -1


# ---------------------------------------------------------------------------
# _source_queue_url
# ---------------------------------------------------------------------------


class TestSourceQueueUrl:
    def test_replaces_dlq_suffix_with_queue(self) -> None:
        url = "https://sqs.us-east-1.amazonaws.com/123/matik-scrb-high-production-dlq"
        assert _source_queue_url(url).endswith("matik-scrb-high-production-queue")

    def test_trailing_slash_handled(self) -> None:
        url = "https://sqs.aws.com/123/my-dlq/"
        assert _source_queue_url(url) == "https://sqs.aws.com/123/my-queue"

    def test_non_dlq_url_returned_unchanged(self) -> None:
        url = "https://sqs.aws.com/123/something-else"
        assert _source_queue_url(url) == url

    def test_real_catalogue_url(self) -> None:
        _, dlq_url = DLQ_CATALOGUE["staging"][3]  # Enricher
        assert _source_queue_url(dlq_url).endswith("matik-enr-enrichment-staging-queue")

    def test_every_catalogue_dlq_maps_to_a_queue(self) -> None:
        for _env, queues in DLQ_CATALOGUE.items():
            for _label, dlq_url in queues:
                assert _source_queue_url(dlq_url).endswith("-queue")


# ---------------------------------------------------------------------------
# _queue_arn
# ---------------------------------------------------------------------------


class TestQueueArn:
    def test_returns_arn_from_attributes(self) -> None:
        sqs = MagicMock()
        sqs.get_queue_attributes.return_value = {
            "Attributes": {"QueueArn": "arn:aws:sqs:us-east-1:123:my-dlq"}
        }
        assert _queue_arn(sqs, "https://sqs.aws.com/123/my-dlq") == (
            "arn:aws:sqs:us-east-1:123:my-dlq"
        )

    def test_requests_queue_arn_attribute(self) -> None:
        sqs = MagicMock()
        sqs.get_queue_attributes.return_value = {"Attributes": {"QueueArn": "arn:x"}}
        _queue_arn(sqs, "https://sqs.aws.com/123/my-dlq")
        assert sqs.get_queue_attributes.call_args[1]["AttributeNames"] == ["QueueArn"]


# ---------------------------------------------------------------------------
# _match_filter
# ---------------------------------------------------------------------------


class TestMatchFilter:
    def test_no_filters_matches_everything(self) -> None:
        assert _match_filter({"source_type": "ghe_pr"}, None, None) is True
        assert _match_filter("not a dict", None, None) is True

    def test_source_type_match(self) -> None:
        body = {"source_type": "ghe_pr", "message_type": "enrichment"}
        assert _match_filter(body, "ghe_pr", None) is True
        assert _match_filter(body, "jira", None) is False

    def test_message_type_match(self) -> None:
        body = {"source_type": "ghe_pr", "message_type": "enrichment"}
        assert _match_filter(body, None, "enrichment") is True
        assert _match_filter(body, None, "write") is False

    def test_both_filters_must_match(self) -> None:
        body = {"source_type": "ghe_pr", "message_type": "enrichment"}
        assert _match_filter(body, "ghe_pr", "enrichment") is True
        assert _match_filter(body, "ghe_pr", "write") is False

    def test_non_dict_body_fails_when_filter_set(self) -> None:
        assert _match_filter("not json", "ghe_pr", None) is False

    def test_missing_field_does_not_match(self) -> None:
        assert _match_filter({"message_type": "enrichment"}, "ghe_pr", None) is False


# ---------------------------------------------------------------------------
# _redrive_manual
# ---------------------------------------------------------------------------


class TestRedriveManual:
    def _make_sqs(self, batches: list[list[dict[str, Any]]]) -> MagicMock:
        sqs = MagicMock()
        responses = [{"Messages": b} for b in batches] + [
            {"Messages": []} for _ in range(_MAX_CONSECUTIVE_EMPTY_POLLS + 1)
        ]
        sqs.receive_message.side_effect = responses
        return sqs

    def test_sends_to_source_and_deletes(self) -> None:
        sqs = self._make_sqs([[_make_msg("m1")]])
        result = _redrive_manual(
            sqs,
            "https://sqs.aws.com/123/q-dlq",
            "https://sqs.aws.com/123/q-queue",
            source_type=None,
            message_type=None,
            limit=10,
            dry_run=False,
            rate=None,
        )
        assert result["redriven"] == 1
        assert sqs.send_message.call_args[1]["QueueUrl"] == (
            "https://sqs.aws.com/123/q-queue"
        )
        sqs.delete_message.assert_called_once()

    def test_deletes_only_after_successful_send(self) -> None:
        sqs = self._make_sqs([[_make_msg("m1")]])
        sqs.send_message.side_effect = Exception("send failed")
        result = _redrive_manual(
            sqs,
            "https://sqs.aws.com/123/q-dlq",
            "https://sqs.aws.com/123/q-queue",
            source_type=None,
            message_type=None,
            limit=10,
            dry_run=False,
            rate=None,
        )
        assert result["failed"] == 1
        assert result["redriven"] == 0
        sqs.delete_message.assert_not_called()

    def test_dry_run_sends_and_deletes_nothing(self) -> None:
        sqs = self._make_sqs([[_make_msg("m1"), _make_msg("m2")]])
        result = _redrive_manual(
            sqs,
            "https://sqs.aws.com/123/q-dlq",
            "https://sqs.aws.com/123/q-queue",
            source_type=None,
            message_type=None,
            limit=10,
            dry_run=True,
            rate=None,
        )
        assert result["matched"] == 2
        assert result["redriven"] == 0
        sqs.send_message.assert_not_called()
        sqs.delete_message.assert_not_called()

    def test_respects_limit(self) -> None:
        batch = [_make_msg(f"m{i}") for i in range(10)]
        sqs = MagicMock()

        def _receive(**kwargs: Any) -> dict[str, Any]:
            n = kwargs.get("MaxNumberOfMessages", 10)
            return {"Messages": batch[:n]}

        sqs.receive_message.side_effect = _receive
        result = _redrive_manual(
            sqs,
            "https://sqs.aws.com/123/q-dlq",
            "https://sqs.aws.com/123/q-queue",
            source_type=None,
            message_type=None,
            limit=3,
            dry_run=False,
            rate=None,
        )
        assert result["redriven"] == 3

    def test_filter_skips_non_matching(self) -> None:
        msgs = [
            _make_msg("m1", {"source_type": "ghe_pr", "message_type": "enrichment"}),
            _make_msg("m2", {"source_type": "jira", "message_type": "enrichment"}),
        ]
        sqs = self._make_sqs([msgs])
        result = _redrive_manual(
            sqs,
            "https://sqs.aws.com/123/q-dlq",
            "https://sqs.aws.com/123/q-queue",
            source_type="ghe_pr",
            message_type=None,
            limit=10,
            dry_run=False,
            rate=None,
        )
        assert result["redriven"] == 1
        assert result["skipped"] == 1

    def test_preserves_message_attributes(self) -> None:
        msg = _make_msg("m1")
        msg["MessageAttributes"] = {"foo": {"DataType": "String", "StringValue": "bar"}}
        sqs = self._make_sqs([[msg]])
        _redrive_manual(
            sqs,
            "https://sqs.aws.com/123/q-dlq",
            "https://sqs.aws.com/123/q-queue",
            source_type=None,
            message_type=None,
            limit=10,
            dry_run=False,
            rate=None,
        )
        assert "MessageAttributes" in sqs.send_message.call_args[1]

    def test_returns_destination(self) -> None:
        sqs = self._make_sqs([[]])
        result = _redrive_manual(
            sqs,
            "https://sqs.aws.com/123/q-dlq",
            "https://sqs.aws.com/123/q-queue",
            source_type=None,
            message_type=None,
            limit=10,
            dry_run=False,
            rate=None,
        )
        assert result["destination"] == "https://sqs.aws.com/123/q-queue"

    def test_run_id_stamped_as_message_attribute(self) -> None:
        sqs = self._make_sqs([[_make_msg("m1")]])
        _redrive_manual(
            sqs,
            "https://sqs.aws.com/123/q-dlq",
            "https://sqs.aws.com/123/q-queue",
            source_type=None,
            message_type=None,
            limit=10,
            dry_run=False,
            rate=None,
            run_id="20260813_120000",
        )
        attrs = sqs.send_message.call_args[1]["MessageAttributes"]
        assert attrs["RedriveRunId"] == {
            "DataType": "String",
            "StringValue": "20260813_120000",
        }

    def test_run_id_merged_with_existing_attributes(self) -> None:
        msg = _make_msg("m1")
        msg["MessageAttributes"] = {"foo": {"DataType": "String", "StringValue": "bar"}}
        sqs = self._make_sqs([[msg]])
        _redrive_manual(
            sqs,
            "https://sqs.aws.com/123/q-dlq",
            "https://sqs.aws.com/123/q-queue",
            source_type=None,
            message_type=None,
            limit=10,
            dry_run=False,
            rate=None,
            run_id="run-1",
        )
        attrs = sqs.send_message.call_args[1]["MessageAttributes"]
        assert attrs["foo"] == {"DataType": "String", "StringValue": "bar"}
        assert attrs["RedriveRunId"]["StringValue"] == "run-1"

    def test_no_run_id_means_no_redrive_attribute(self) -> None:
        sqs = self._make_sqs([[_make_msg("m1")]])
        _redrive_manual(
            sqs,
            "https://sqs.aws.com/123/q-dlq",
            "https://sqs.aws.com/123/q-queue",
            source_type=None,
            message_type=None,
            limit=10,
            dry_run=False,
            rate=None,
        )
        assert "MessageAttributes" not in sqs.send_message.call_args[1]

    def test_does_not_stop_on_single_short_batch(self) -> None:
        """A single empty poll must not end the redrive early — see the
        _peek_dlq test of the same name for why (SQS short-polling can
        return an empty/short batch without the queue being drained)."""
        batch1 = [_make_msg(f"m{i}") for i in range(2)]
        batch2 = [_make_msg(f"n{i}") for i in range(3)]
        sqs = self._make_sqs([batch1, [], batch2])
        result = _redrive_manual(
            sqs,
            "https://sqs.aws.com/123/q-dlq",
            "https://sqs.aws.com/123/q-queue",
            source_type=None,
            message_type=None,
            limit=10,
            dry_run=False,
            rate=None,
        )
        assert result["redriven"] == 5

    def test_uses_long_polling(self) -> None:
        sqs = self._make_sqs([[_make_msg("m1")]])
        _redrive_manual(
            sqs,
            "https://sqs.aws.com/123/q-dlq",
            "https://sqs.aws.com/123/q-queue",
            source_type=None,
            message_type=None,
            limit=10,
            dry_run=False,
            rate=None,
        )
        call_kwargs = sqs.receive_message.call_args_list[0][1]
        assert call_kwargs.get("WaitTimeSeconds") == 20


# ---------------------------------------------------------------------------
# _redrive_native
# ---------------------------------------------------------------------------


class TestRedriveNative:
    def test_passes_source_arn(self) -> None:
        sqs = MagicMock()
        sqs.start_message_move_task.return_value = {"TaskHandle": "handle-1"}
        result = _redrive_native(sqs, "arn:aws:sqs:us-east-1:123:q-dlq", rate=None)
        assert sqs.start_message_move_task.call_args[1]["SourceArn"] == (
            "arn:aws:sqs:us-east-1:123:q-dlq"
        )
        assert result["task_handle"] == "handle-1"

    def test_omits_rate_when_none(self) -> None:
        sqs = MagicMock()
        sqs.start_message_move_task.return_value = {"TaskHandle": "h"}
        _redrive_native(sqs, "arn:x", rate=None)
        assert (
            "MaxNumberOfMessagesPerSecond"
            not in (sqs.start_message_move_task.call_args[1])
        )

    def test_passes_rate_when_set(self) -> None:
        sqs = MagicMock()
        sqs.start_message_move_task.return_value = {"TaskHandle": "h"}
        _redrive_native(sqs, "arn:x", rate=5)
        call_kwargs = sqs.start_message_move_task.call_args[1]
        assert call_kwargs["MaxNumberOfMessagesPerSecond"] == 5


# ---------------------------------------------------------------------------
# Redrive prompts
# ---------------------------------------------------------------------------


class TestRedrivePrompts:
    def test_ask_action_default_inspect(self) -> None:
        with patch("builtins.input", return_value=""):
            assert _ask_action() == "inspect"

    def test_ask_action_redrive_by_name(self) -> None:
        with patch("builtins.input", return_value="redrive"):
            assert _ask_action() == "redrive"

    def test_ask_action_redrive_by_number(self) -> None:
        with patch("builtins.input", return_value="2"):
            assert _ask_action() == "redrive"

    def test_ask_action_unknown_defaults_inspect(self) -> None:
        with patch("builtins.input", return_value="bogus"):
            assert _ask_action() == "inspect"

    def test_ask_redrive_mode_default_manual(self) -> None:
        with patch("builtins.input", return_value=""):
            assert _ask_redrive_mode() == "manual"

    def test_ask_redrive_mode_native(self) -> None:
        with patch("builtins.input", return_value="native"):
            assert _ask_redrive_mode() == "native"

    def test_ask_redrive_filters_empty_means_none(self) -> None:
        with patch("builtins.input", side_effect=["", ""]):
            assert _ask_redrive_filters() == (None, None)

    def test_ask_redrive_filters_values(self) -> None:
        with patch("builtins.input", side_effect=["ghe_pr", "enrichment"]):
            assert _ask_redrive_filters() == ("ghe_pr", "enrichment")

    def test_ask_redrive_limit_default(self) -> None:
        with patch("builtins.input", return_value=""):
            assert _ask_redrive_limit() == DEFAULT_MAX_MESSAGES

    def test_ask_redrive_limit_value(self) -> None:
        with patch("builtins.input", return_value="5"):
            assert _ask_redrive_limit() == 5

    def test_ask_redrive_limit_invalid_falls_back(self) -> None:
        with patch("builtins.input", return_value="abc"):
            assert _ask_redrive_limit() == DEFAULT_MAX_MESSAGES

    def test_ask_dry_run_default_yes(self) -> None:
        with patch("builtins.input", return_value=""):
            assert _ask_dry_run() is True

    def test_ask_dry_run_no(self) -> None:
        with patch("builtins.input", return_value="n"):
            assert _ask_dry_run() is False

    def test_ask_throttle_empty_is_none(self) -> None:
        with patch("builtins.input", return_value=""):
            assert _ask_throttle() is None

    def test_ask_throttle_value(self) -> None:
        with patch("builtins.input", return_value="10"):
            assert _ask_throttle() == 10

    def test_ask_throttle_invalid_is_none(self) -> None:
        with patch("builtins.input", return_value="fast"):
            assert _ask_throttle() is None
