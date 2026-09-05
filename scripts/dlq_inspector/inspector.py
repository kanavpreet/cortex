"""
DLQ Inspector — inspect and redrive messages from Matik SQS dead letter queues.

USAGE
-----
From the scripts/ directory (recommended):
    uv run python -m dlq_inspector

Or via the top-level dispatcher:
    uv run python -m scripts dlq-inspector

WHAT IT DOES
------------
The tool is interactive. It first asks for an environment (sandbox / staging /
production), which DLQ(s) to act on (individual or all at once), and then an
action: inspect (read-only, the default) or redrive.

INSPECT (read-only)
    1. Reads up to a configurable max number of messages from each selected DLQ
       (or every message, if 'all' is chosen) using SQS ReceiveMessage with
       VisibilityTimeout=0 so messages are NOT hidden from other consumers and
       are NOT deleted — this is a pure peek. If a queue's approximate depth
       exceeds the chosen cap, the tool warns and suggests using 'all'.
    2. Saves each message as a JSON file under:
           dlq-output/<env>/<queue-name>/<timestamp>_<message-id>.json
    3. Generates a Markdown report at:
           dlq-output/<env>/report_<timestamp>.md
       summarising per-queue counts, source_type/message_type breakdowns, and a
       sample of the raw payloads.

REDRIVE (moves messages back to the source queue)
    Source queues are derived from the DLQ name by the Matik convention
    (`-dlq` -> `-queue`), which matches the redrive policy already attached to
    each DLQ.  Two modes:

      manual (default) — receive → filter → send to source → delete from DLQ,
        one message at a time.  Offers fine control:
          • filter by source_type and/or message_type
          • cap the number of messages redriven per queue
          • dry run (default): show what WOULD move without changing anything
          • throttle in messages/second
        Messages are deleted from the DLQ only after a successful send, so a
        failure cannot lose data.

      native — AWS StartMessageMoveTask.  Fast; moves ALL messages back using
        the redrive policy.  Supports a messages/second throttle but NO
        per-message filtering.

    Redrive is destructive (it deletes from the DLQ).  Production requires the
    same extra confirmation gate as inspection, plus a per-run confirm summary
    that shows the derived destination for each queue.

PREREQUISITES
-------------
- AWS CLI v2 installed and on PATH  (brew install awscli)
- Valid AWS credentials in your environment.  The script uses whatever profile
  boto3 resolves by default (env vars > ~/.aws/credentials > instance metadata).
  To use a specific profile, set AWS_PROFILE before running:
      AWS_PROFILE=airbnb uv run python -m dlq_inspector

DEPENDENCIES
------------
boto3 is NOT listed in scripts/pyproject.toml by default because it is a heavy
dependency not needed by other scripts.  If you get an ImportError, add it:
    cd scripts && uv add boto3

The script detects the missing import and prints a helpful error rather than
crashing with a traceback.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from common.console import (
    COLOR_BOLD,
    COLOR_RESET,
    print_error,
    print_info,
    print_progress,
    print_success,
    print_warning,
)

# ---------------------------------------------------------------------------
# Queue catalogue — sourced from _infra/kube/kube-gen.yml
# ---------------------------------------------------------------------------
# Each entry: (label shown to user, queue_name_suffix used in the URL, dlq_url)
# The DLQ URLs are hardcoded from kube-gen.yml.  If new queues are added,
# update this dict.

ACCOUNT_ID = "172631448019"
REGION = "us-east-1"

# Structure: env -> list of (label, dlq_url)
DLQ_CATALOGUE: dict[str, list[tuple[str, str]]] = {
    "sandbox": [
        (
            "Scribe high-priority",
            f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT_ID}/matik-scrb-high-sandbox-dlq",
        ),
        (
            "Scribe medium-priority",
            f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT_ID}/matik-scrb-medium-sandbox-dlq",
        ),
        (
            "Scribe low-priority",
            f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT_ID}/matik-scrb-low-sandbox-dlq",
        ),
        (
            "Enricher",
            f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT_ID}/matik-enr-enrichment-sandbox-dlq",
        ),
        (
            "Enigmatologist",
            f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT_ID}/matik-enig-triggers-sandbox-dlq",
        ),
    ],
    "staging": [
        (
            "Scribe high-priority",
            f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT_ID}/matik-scrb-high-staging-dlq",
        ),
        (
            "Scribe medium-priority",
            f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT_ID}/matik-scrb-medium-staging-dlq",
        ),
        (
            "Scribe low-priority",
            f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT_ID}/matik-scrb-low-staging-dlq",
        ),
        (
            "Enricher",
            f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT_ID}/matik-enr-enrichment-staging-dlq",
        ),
        (
            "Enigmatologist",
            f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT_ID}/matik-enig-triggers-staging-dlq",
        ),
    ],
    "production": [
        (
            "Scribe high-priority",
            f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT_ID}/matik-scrb-high-production-dlq",
        ),
        (
            "Scribe medium-priority",
            f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT_ID}/matik-scrb-medium-production-dlq",
        ),
        (
            "Scribe low-priority",
            f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT_ID}/matik-scrb-low-production-dlq",
        ),
        (
            "Enricher",
            f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT_ID}/matik-enr-enrichment-production-dlq",
        ),
        (
            "Enigmatologist",
            f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT_ID}/matik-enig-triggers-production-dlq",
        ),
    ],
}

# Maximum messages to pull per DLQ in a single run.  SQS ReceiveMessage returns
# at most 10 per call; we loop until this limit is hit or the queue drains.
DEFAULT_MAX_MESSAGES = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bold(text: str) -> str:
    return f"{COLOR_BOLD}{text}{COLOR_RESET}"


def _ask(prompt: str) -> str:
    """Print a prompt and return stripped user input."""
    return input(f"\n{COLOR_BOLD}{prompt}{COLOR_RESET} ").strip()


def _ask_env() -> str:
    """Interactively ask which environment to inspect."""
    envs = list(DLQ_CATALOGUE.keys())
    print(f"\n{_bold('Available environments:')}")
    for i, env in enumerate(envs, 1):
        print(f"  {i}. {env}")

    while True:
        choice = _ask("Enter environment name or number:")
        if choice in envs:
            return choice
        if choice.isdigit() and 1 <= int(choice) <= len(envs):
            return envs[int(choice) - 1]
        print_warning(f"Invalid choice '{choice}'. Please enter a name or number.")


def _ask_queues(env: str) -> list[tuple[str, str]]:
    """Interactively ask which DLQs to inspect for the given environment."""
    queues = DLQ_CATALOGUE[env]

    print(f"\n{_bold(f'Available DLQs for {env}:')}")
    for i, (label, url) in enumerate(queues, 1):
        queue_name = url.split("/")[-1]
        print(f"  {i}. {label}  ({queue_name})")
    print(f"  {len(queues) + 1}. All of the above")

    while True:
        choice = _ask("Enter queue number(s) separated by commas, or 'all':")

        if choice.lower() in ("all", str(len(queues) + 1)):
            return queues

        selected: list[tuple[str, str]] = []
        try:
            indices = [int(c.strip()) for c in choice.split(",")]
            for idx in indices:
                if not (1 <= idx <= len(queues)):
                    raise ValueError(f"index {idx} out of range")
                selected.append(queues[idx - 1])
            return selected
        except ValueError as e:
            print_warning(f"Invalid selection: {e}. Try again.")


def _ask_max_messages() -> int | None:
    """
    Ask how many messages to pull (defaults to DEFAULT_MAX_MESSAGES).

    Returns None to mean "no cap — retrieve every message in the queue".
    """
    prompt = (
        f"Max messages to pull per queue [{DEFAULT_MAX_MESSAGES}] "
        "(Enter for default, or 'all' to retrieve every message):"
    )
    choice = _ask(prompt)
    if not choice:
        return DEFAULT_MAX_MESSAGES
    if choice.lower() in ("all", "a"):
        return None
    try:
        value = int(choice)
        if value < 1:
            raise ValueError("must be >= 1")
        return value
    except ValueError:
        print_warning(f"Invalid number, using default ({DEFAULT_MAX_MESSAGES}).")
        return DEFAULT_MAX_MESSAGES


def _confirm_production(env: str) -> bool:
    """Extra confirmation gate for production to avoid accidental runs."""
    if env != "production":
        return True
    print_warning(
        "You are about to inspect PRODUCTION DLQs.  "
        "Messages will be read but NOT deleted."
    )
    answer = _ask("Type 'yes' to continue:")
    return answer.lower() == "yes"


def _ask_action() -> str:
    """Ask whether to inspect (read-only) or redrive (move messages back)."""
    print(f"\n{_bold('What would you like to do?')}")
    print("  1. inspect  — read-only peek + report (default)")
    print("  2. redrive  — move messages from the DLQ back to its source queue")

    choice = _ask("Enter action [inspect]:")
    if not choice or choice.lower() in ("1", "inspect"):
        return "inspect"
    if choice.lower() in ("2", "redrive"):
        return "redrive"
    print_warning(f"Unrecognised action '{choice}', defaulting to inspect.")
    return "inspect"


def _ask_redrive_mode() -> str:
    """Ask which redrive mechanism to use."""
    print(f"\n{_bold('Redrive mode:')}")
    print(
        "  1. manual  — receive/filter/send/delete one-by-one (default).\n"
        "               Supports source_type/message_type filters, a cap,\n"
        "               dry-run, and throttling."
    )
    print(
        "  2. native  — AWS StartMessageMoveTask. Fast, moves ALL messages\n"
        "               using the redrive policy. No per-message filtering."
    )

    choice = _ask("Enter mode [manual]:")
    if not choice or choice.lower() in ("1", "manual"):
        return "manual"
    if choice.lower() in ("2", "native"):
        return "native"
    print_warning(f"Unrecognised mode '{choice}', defaulting to manual.")
    return "manual"


def _ask_redrive_filters() -> tuple[str | None, str | None]:
    """
    Ask for optional source_type / message_type filters (manual mode only).

    Empty input means "no filter on this field". Matching is exact against the
    same body fields _analyse_messages reads.
    """
    source_type = _ask("Filter by source_type (Enter for all):") or None
    message_type = _ask("Filter by message_type (Enter for all):") or None
    return source_type, message_type


def _ask_redrive_limit() -> int | None:
    """
    Ask for the maximum number of messages to redrive (defaults to the cap).

    Returns None to mean "no cap — redrive every message in the queue".
    """
    prompt = (
        f"Max messages to redrive per queue [{DEFAULT_MAX_MESSAGES}] "
        "(Enter for default, or 'all' to redrive every message):"
    )
    choice = _ask(prompt)
    if not choice:
        return DEFAULT_MAX_MESSAGES
    if choice.lower() in ("all", "a"):
        return None
    try:
        value = int(choice)
        if value < 1:
            raise ValueError("must be >= 1")
        return value
    except ValueError:
        print_warning(f"Invalid number, using default ({DEFAULT_MAX_MESSAGES}).")
        return DEFAULT_MAX_MESSAGES


def _ask_dry_run() -> bool:
    """Ask whether to do a dry run (manual mode). Defaults to yes for safety."""
    answer = _ask("Dry run first (show what would move, change nothing)? [Y/n]:")
    return answer.lower() not in ("n", "no")


def _ask_throttle() -> int | None:
    """Ask for an optional throttle rate in messages/second (Enter = unthrottled)."""
    choice = _ask("Throttle rate in messages/second (Enter for no limit):")
    if not choice:
        return None
    try:
        value = int(choice)
        if value < 1:
            raise ValueError("must be >= 1")
        return value
    except ValueError:
        print_warning("Invalid rate, proceeding without throttling.")
        return None


# ---------------------------------------------------------------------------
# SQS interaction
# ---------------------------------------------------------------------------


# SQS is a distributed queue: a ReceiveMessage call can return fewer messages
# than requested (or none) even when the queue is not actually drained, because
# short polling (WaitTimeSeconds=0) only samples a subset of the backing
# servers per call — AWS's own guidance is to use long polling to reliably
# enumerate/drain a queue. We long-poll (see _LONG_POLL_WAIT_SECONDS below) AND
# still require several consecutive polls with no *new* messages before
# concluding the queue is drained, since even long polling isn't guaranteed to
# hit every server on a single call.
_MAX_CONSECUTIVE_EMPTY_POLLS = 3

# Max allowed value for SQS ReceiveMessage's WaitTimeSeconds. Using long
# polling (>0) makes SQS query all servers before responding, which is what
# actually fixes the "queue reports depth=53 but we only ever collect ~25-32"
# under-count — short polling can systematically miss messages no matter how
# many times you retry.
_LONG_POLL_WAIT_SECONDS = 20


def _progress_total(limit: int | None, depth: int | None) -> int | None:
    """Derive a progress-bar total from a cap and/or a queue's approximate depth."""
    if limit is not None and depth is not None and depth >= 0:
        return min(limit, depth)
    if limit is not None:
        return limit
    if depth is not None and depth >= 0:
        return depth
    return None


def _peek_dlq(
    sqs: Any,
    queue_url: str,
    max_messages: int | None,
    depth: int | None = None,
) -> list[dict[str, Any]]:
    """
    Read up to max_messages from a DLQ without deleting or hiding them.

    Uses VisibilityTimeout=0 so messages immediately become visible again
    after being received — this is a non-destructive peek.

    Pass max_messages=None to retrieve every message currently in the queue
    (no cap). SQS returns at most 10 messages per call, so we loop, stopping
    once max_messages is reached or the queue looks drained (see
    _MAX_CONSECUTIVE_EMPTY_POLLS).

    depth (the queue's approximate size, if already known) is used only to
    size the progress bar; it does not affect what gets collected.
    """
    collected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    consecutive_empty_polls = 0
    total = _progress_total(max_messages, depth)

    while max_messages is None or len(collected) < max_messages:
        if max_messages is None:
            batch_size = 10
        else:
            batch_size = min(10, max_messages - len(collected))
        response = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=batch_size,
            # VisibilityTimeout=0: message stays visible immediately after receipt.
            # This means the same message CAN be returned on multiple iterations if
            # SQS decides to re-deliver it.  We deduplicate by MessageId.
            VisibilityTimeout=0,
            # Long poll so SQS queries all backing servers instead of a subset;
            # without this, real messages can be missed no matter how many
            # times we retry (see module-level comment on _LONG_POLL_WAIT_SECONDS).
            WaitTimeSeconds=_LONG_POLL_WAIT_SECONDS,
            AttributeNames=["All"],
            MessageAttributeNames=["All"],
        )

        messages = response.get("Messages", [])

        new_count = 0
        for msg in messages:
            msg_id = msg.get("MessageId", "")
            if msg_id not in seen_ids:
                seen_ids.add(msg_id)
                collected.append(msg)
                new_count += 1

        print_progress(len(collected), total, prefix="  peeking")

        if new_count == 0:
            consecutive_empty_polls += 1
            if consecutive_empty_polls >= _MAX_CONSECUTIVE_EMPTY_POLLS:
                break
        else:
            consecutive_empty_polls = 0

    if collected:
        print()
    return collected


def _get_queue_depth(sqs: Any, queue_url: str) -> int:
    """Return the approximate number of messages in the queue."""
    try:
        response = sqs.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=["ApproximateNumberOfMessages"],
        )
        return int(response["Attributes"].get("ApproximateNumberOfMessages", 0))
    except Exception:
        return -1  # Unknown — attribute fetch failed


# ---------------------------------------------------------------------------
# Redrive
# ---------------------------------------------------------------------------


def _match_filter(
    body: Any,
    source_type: str | None,
    message_type: str | None,
) -> bool:
    """
    Return True if a message body matches the (optional) filters.

    A None filter matches everything. Comparison is exact against the same
    source_type / message_type fields _analyse_messages reads. A non-dict or
    unparseable body only matches when no filters are set.
    """
    if source_type is None and message_type is None:
        return True
    if not isinstance(body, dict):
        return False
    if source_type is not None and body.get("source_type") != source_type:
        return False
    if message_type is not None and body.get("message_type") != message_type:
        return False
    return True


def _redrive_manual(
    sqs: Any,
    dlq_url: str,
    source_url: str,
    *,
    source_type: str | None,
    message_type: str | None,
    limit: int | None,
    dry_run: bool,
    rate: int | None,
    depth: int | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """
    Redrive messages from a DLQ back to its source queue one at a time.

    For each message we send a copy to the source queue (preserving the body and
    any message attributes), then delete it from the DLQ — deleting only AFTER a
    successful send so a failure can't lose data. Messages are received with a
    non-zero VisibilityTimeout so they are not re-delivered to us mid-redrive.

    In dry_run mode nothing is sent or deleted; we only count what would move.

    Pass limit=None to redrive every message currently in the queue (no cap).
    depth (the queue's approximate size, if already known) is used only to
    size the progress bar.

    run_id, if given, is stamped on each redriven message as a RedriveRunId
    message attribute (alongside any attributes already on the message) so
    the consumer that eventually reprocesses it can tag its own success/failure
    metrics and logs with the same value — letting you trace a specific redrive
    run through to its outcome.

    Returns a dict with: destination, matched, redriven, skipped, failed.
    """
    matched = 0
    redriven = 0
    skipped = 0
    failed = 0
    sleep_s = 1.0 / rate if rate else 0.0
    consecutive_empty_polls = 0
    total = _progress_total(limit, depth)
    progress_prefix = "  dry run" if dry_run else "  redriving"

    while limit is None or matched + skipped < limit:
        batch_size = 10 if limit is None else min(10, limit - matched - skipped)
        response = sqs.receive_message(
            QueueUrl=dlq_url,
            MaxNumberOfMessages=batch_size,
            # Hide messages we are about to move so SQS doesn't hand them back to
            # us (or another consumer) before we delete them.
            VisibilityTimeout=30,
            # Long poll so SQS queries all backing servers instead of a subset
            # (see _LONG_POLL_WAIT_SECONDS comment on _peek_dlq) — without this,
            # real messages can be left behind in the DLQ no matter how many
            # times we retry.
            WaitTimeSeconds=_LONG_POLL_WAIT_SECONDS,
            AttributeNames=["All"],
            MessageAttributeNames=["All"],
        )

        messages = response.get("Messages", [])
        if not messages:
            consecutive_empty_polls += 1
            if consecutive_empty_polls >= _MAX_CONSECUTIVE_EMPTY_POLLS:
                break  # Queue drained
            continue
        consecutive_empty_polls = 0

        for msg in messages:
            if limit is not None and matched + skipped >= limit:
                break

            body_str = msg.get("Body", "")
            try:
                body = json.loads(body_str)
            except (json.JSONDecodeError, ValueError):
                body = body_str

            if not _match_filter(body, source_type, message_type):
                skipped += 1
                continue

            matched += 1

            if dry_run:
                continue

            try:
                send_kwargs: dict[str, Any] = {
                    "QueueUrl": source_url,
                    "MessageBody": body_str,
                }
                attrs = msg.get("MessageAttributes")
                if run_id:
                    attrs = dict(attrs) if attrs else {}
                    attrs["RedriveRunId"] = {
                        "DataType": "String",
                        "StringValue": run_id,
                    }
                if attrs:
                    send_kwargs["MessageAttributes"] = attrs
                sqs.send_message(**send_kwargs)
                sqs.delete_message(
                    QueueUrl=dlq_url,
                    ReceiptHandle=msg["ReceiptHandle"],
                )
                redriven += 1
            except Exception as e:
                failed += 1
                print_error(f"  Failed to redrive message {msg.get('MessageId')}: {e}")

            print_progress(matched + skipped, total, prefix=progress_prefix)

            if sleep_s:
                time.sleep(sleep_s)

    if matched + skipped:
        print()

    return {
        "destination": source_url,
        "matched": matched,
        "redriven": redriven,
        "skipped": skipped,
        "failed": failed,
    }


def _redrive_native(
    sqs: Any,
    dlq_arn: str,
    *,
    rate: int | None,
) -> dict[str, Any]:
    """
    Start an AWS native message-move task to drain a DLQ via its redrive policy.

    This moves ALL messages back to the source queue the DLQ is associated with;
    there is no per-message filtering. An optional rate caps messages/second.

    Returns a dict with the task handle (and destination set to the policy's
    configured source, which AWS resolves automatically).
    """
    kwargs: dict[str, Any] = {"SourceArn": dlq_arn}
    if rate:
        kwargs["MaxNumberOfMessagesPerSecond"] = rate
    response = sqs.start_message_move_task(**kwargs)
    return {
        "destination": "redrive-policy source queue",
        "task_handle": response.get("TaskHandle", ""),
    }


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _queue_name(url: str) -> str:
    """Extract the queue name from a URL."""
    return url.rstrip("/").split("/")[-1]


def _source_queue_url(dlq_url: str) -> str:
    """
    Derive the source (main) queue URL from a DLQ URL.

    The Matik convention (see _infra/kube/kube-gen.yml) is that a source queue
    has the same name as its DLQ with the trailing '-dlq' replaced by '-queue':
        matik-scrb-high-production-dlq -> matik-scrb-high-production-queue

    The DLQ's redrive policy already associates it with this source queue, so
    redriven messages land back where they were originally delivered.
    """
    url = dlq_url.rstrip("/")
    if url.endswith("-dlq"):
        return url[: -len("-dlq")] + "-queue"
    # Unexpected naming — fall back to appending nothing so the caller can spot
    # the mismatch rather than silently sending to the wrong place.
    return url


def _queue_arn(sqs: Any, queue_url: str) -> str:
    """Return the ARN for a queue, needed for native message-move tasks."""
    response = sqs.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=["QueueArn"],
    )
    arn: str = response["Attributes"]["QueueArn"]
    return arn


def _save_messages(
    messages: list[dict[str, Any]],
    env: str,
    queue_url: str,
    run_ts: str,
    output_root: Path,
) -> Path:
    """
    Save each message as an individual JSON file.

    Directory layout:
        dlq-output/<env>/<queue-name>/<run_ts>_<message-id>.json

    Each file contains the full raw SQS message dict plus a 'parsed_body'
    key with the decoded JSON body (if the body is valid JSON).
    """
    queue_name = _queue_name(queue_url)
    out_dir = output_root / env / queue_name
    out_dir.mkdir(parents=True, exist_ok=True)

    for msg in messages:
        msg_id = msg.get("MessageId", "unknown")
        body_str = msg.get("Body", "")

        # Attempt to parse the body so the saved file is easier to read
        try:
            parsed_body = json.loads(body_str)
        except (json.JSONDecodeError, ValueError):
            parsed_body = None

        record = {**msg, "parsed_body": parsed_body}
        filename = out_dir / f"{run_ts}_{msg_id}.json"
        filename.write_text(json.dumps(record, indent=2, default=str))

    return out_dir


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def _analyse_messages(
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Extract summary statistics from a list of SQS messages.

    Returns a dict with:
      - total: int
      - source_types: Counter-like dict {source_type: count}
      - message_types: Counter-like dict {message_type: count}
      - error_indicators: list of strings hinting at the error (best-effort)
      - samples: up to 3 abbreviated message bodies for the report
    """
    source_types: dict[str, int] = {}
    message_types: dict[str, int] = {}
    samples: list[str] = []

    for msg in messages:
        body_str = msg.get("Body", "")
        try:
            body = json.loads(body_str)
        except (json.JSONDecodeError, ValueError):
            body = {}

        st = body.get("source_type", "unknown") if isinstance(body, dict) else "unknown"
        mt = (
            body.get("message_type", "unknown") if isinstance(body, dict) else "unknown"
        )

        source_types[st] = source_types.get(st, 0) + 1
        message_types[mt] = message_types.get(mt, 0) + 1

        if len(samples) < 3:
            # Truncate very large bodies for the report so it stays readable
            body_repr = json.dumps(body, default=str)
            if len(body_repr) > 800:
                body_repr = body_repr[:800] + "... (truncated)"
            samples.append(body_repr)

    return {
        "total": len(messages),
        "source_types": source_types,
        "message_types": message_types,
        "samples": samples,
    }


def _generate_report(
    env: str,
    results: list[tuple[str, str, int, list[dict[str, Any]]]],
    run_ts: str,
    output_root: Path,
    max_messages: int | None,
) -> Path:
    """
    Write a Markdown report summarising all inspected DLQs.

    Args:
        env: Environment name (sandbox/staging/production)
        results: List of (label, queue_url, approximate_depth, messages)
        run_ts: Timestamp string used in filenames
        output_root: Root output directory
        max_messages: Max messages that were configured for this run, or
            None if the run had no cap ("all")

    Returns:
        Path to the written report file.
    """
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    report_path = output_root / env / f"report_{run_ts}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    max_msgs_display = "all (no cap)" if max_messages is None else str(max_messages)

    lines: list[str] = []
    lines.append(f"# Matik DLQ Inspection Report — {env}")
    lines.append(f"\n**Generated:** {now_str}")
    lines.append(f"**Environment:** {env}")
    lines.append(f"**Max messages per queue:** {max_msgs_display}")
    lines.append(f"**Queues inspected:** {len(results)}\n")

    # Overall summary table
    lines.append("## Summary\n")
    lines.append(
        "| Queue | Approx. depth | Messages pulled | source_types | message_types |"
    )
    lines.append(
        "|-------|---------------|-----------------|--------------|---------------|"
    )

    for label, queue_url, depth, messages in results:
        analysis = _analyse_messages(messages)
        depth_str = str(depth) if depth >= 0 else "unknown"
        st_str = ", ".join(
            f"{k}×{v}" for k, v in sorted(analysis["source_types"].items())
        )
        mt_str = ", ".join(
            f"{k}×{v}" for k, v in sorted(analysis["message_types"].items())
        )
        row = (
            f"| {label} | {depth_str} | {analysis['total']}"
            f" | {st_str or '—'} | {mt_str or '—'} |"
        )
        lines.append(row)

    # Per-queue detail sections
    for label, queue_url, depth, messages in results:
        queue_name = _queue_name(queue_url)
        analysis = _analyse_messages(messages)

        lines.append(f"\n---\n\n## {label}\n")
        lines.append(f"**Queue URL:** `{queue_url}`")
        depth_str = str(depth) if depth >= 0 else "unknown"
        lines.append(f"**Approximate depth:** {depth_str}")
        lines.append(f"**Messages pulled:** {analysis['total']}")

        if analysis["total"] == 0:
            lines.append("\n_No messages found in this DLQ._")
            continue

        if analysis["source_types"]:
            lines.append("\n### source_type breakdown\n")
            lines.append("| source_type | count |")
            lines.append("|-------------|-------|")
            for st, count in sorted(analysis["source_types"].items()):
                lines.append(f"| `{st}` | {count} |")

        if analysis["message_types"]:
            lines.append("\n### message_type breakdown\n")
            lines.append("| message_type | count |")
            lines.append("|--------------|-------|")
            for mt, count in sorted(analysis["message_types"].items()):
                lines.append(f"| `{mt}` | {count} |")

        if analysis["samples"]:
            lines.append(f"\n### Sample messages (up to 3 of {analysis['total']})\n")
            for i, sample in enumerate(analysis["samples"], 1):
                lines.append(f"**Message {i}:**\n```json\n{sample}\n```\n")

        lines.append(
            f"\n_Full message files saved to: `dlq-output/{env}/{queue_name}/`_"
        )

    lines.append("\n---\n\n_Report generated by `scripts/dlq_inspector`._\n")

    report_path.write_text("\n".join(lines))
    return report_path


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _run_inspect(
    sqs: Any,
    env: str,
    selected_queues: list[tuple[str, str]],
) -> None:
    """Read-only inspection flow: peek messages, save them, write a report."""
    max_messages = _ask_max_messages()

    # If a cap was chosen, check whether any queue is deeper than that cap and
    # nudge the user toward "all" so they don't end up with a silently partial
    # peek (this is what motivated adding the "all" option in the first place).
    if max_messages is not None:
        deep_queues = []
        for label, queue_url in selected_queues:
            depth = _get_queue_depth(sqs, queue_url)
            if depth > max_messages:
                deep_queues.append((label, depth))
        if deep_queues:
            print_warning(f"Queue depth exceeds the {max_messages}-message cap for:")
            for label, depth in deep_queues:
                print_warning(f"  {label}: approx. depth={depth}")
            print_warning(
                "Re-run and enter 'all' at the max-messages prompt to retrieve "
                "every message and report on the full backlog."
            )

    max_msgs_display = "all (no cap)" if max_messages is None else str(max_messages)

    # Confirm before pulling
    print(f"\n{_bold('About to run:')}")
    print("  Action      : inspect (read-only)")
    print(f"  Environment : {env}")
    print(f"  Queues      : {', '.join(label for label, _ in selected_queues)}")
    print(f"  Max msgs    : {max_msgs_display} per queue")
    confirm = _ask("Proceed? [y/N]:")
    if confirm.lower() not in ("y", "yes"):
        print_info("Aborted.")
        sys.exit(0)

    # Determine output root relative to the CWD (caller decides where to run from)
    output_root = Path("dlq-output")
    run_ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

    results: list[tuple[str, str, int, list[dict[str, Any]]]] = []

    for label, queue_url in selected_queues:
        queue_name = _queue_name(queue_url)
        print_info(f"Inspecting {label} ({queue_name})…")

        try:
            depth = _get_queue_depth(sqs, queue_url)
            messages = _peek_dlq(sqs, queue_url, max_messages, depth)
        except Exception as e:
            print_error(f"Failed to read {queue_name}: {e}")
            results.append((label, queue_url, -1, []))
            continue

        depth_str = str(depth) if depth >= 0 else "unknown"
        print_success(
            f"{label}: approx. depth={depth_str}, pulled {len(messages)} message(s)"
        )

        if messages:
            out_dir = _save_messages(messages, env, queue_url, run_ts, output_root)
            print_info(f"  Saved to {out_dir}/")

        results.append((label, queue_url, depth, messages))

    # Generate Markdown report
    report_path = _generate_report(env, results, run_ts, output_root, max_messages)
    print_success(f"Report written to {report_path}")

    total_pulled = sum(len(msgs) for _, _, _, msgs in results)
    print(
        f"\n{_bold('Done.')} Pulled {total_pulled} message(s) across "
        f"{len(selected_queues)} queue(s).\n"
    )


def _run_redrive(
    sqs: Any,
    env: str,
    selected_queues: list[tuple[str, str]],
) -> None:
    """Redrive flow: move messages from selected DLQs back to their source queues."""
    print_warning(
        "REDRIVE moves messages OUT of the DLQ and back to the source queue.\n"
        "  Unlike inspection, this DELETES messages from the DLQ.  Consider running\n"
        "  an inspection (or a manual dry run) first."
    )

    mode = _ask_redrive_mode()

    source_type: str | None = None
    message_type: str | None = None
    limit: int | None = DEFAULT_MAX_MESSAGES
    dry_run = False
    if mode == "manual":
        source_type, message_type = _ask_redrive_filters()
        limit = _ask_redrive_limit()
        dry_run = _ask_dry_run()
    rate = _ask_throttle()

    # A run_id lets a redriven message's eventual consumer (e.g. Scribe) tag its
    # own success/failure metrics and logs with the same value, so this specific
    # redrive can be traced through to its outcome. Only manual mode can stamp
    # it on messages — native mode moves messages via AWS's own redrive policy
    # without giving us a hook to attach anything.
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S") if mode == "manual" else None

    # Confirm summary — show the derived destination for each DLQ
    limit_display = "all (no cap)" if limit is None else str(limit)
    print(f"\n{_bold('About to run:')}")
    print(f"  Action      : redrive ({mode})")
    print(f"  Environment : {env}")
    if mode == "manual":
        print(f"  source_type : {source_type or 'any'}")
        print(f"  message_type: {message_type or 'any'}")
        print(f"  Max msgs    : {limit_display} per queue")
        print(f"  Dry run     : {'yes' if dry_run else 'NO — will move messages'}")
        print(f"  Redrive ID  : {run_id}  (tag each redriven message carries;")
        print("                grep Scribe's logs/metrics by this to check outcomes)")
    else:
        print_warning(
            "  Native mode cannot tag individual messages — Scribe won't be able\n"
            "  to attribute its metrics/logs to this specific redrive run."
        )
    print(f"  Throttle    : {f'{rate} msg/s' if rate else 'none'}")
    print(f"  {_bold('Queue → destination:')}")
    for label, dlq_url in selected_queues:
        print(f"    {_queue_name(dlq_url)} → {_queue_name(_source_queue_url(dlq_url))}")

    confirm = _ask("Proceed? [y/N]:")
    if confirm.lower() not in ("y", "yes"):
        print_info("Aborted.")
        sys.exit(0)

    for label, dlq_url in selected_queues:
        queue_name = _queue_name(dlq_url)
        source_url = _source_queue_url(dlq_url)
        print_info(f"Redriving {label} ({queue_name}) → {_queue_name(source_url)}…")

        try:
            if mode == "native":
                dlq_arn = _queue_arn(sqs, dlq_url)
                result = _redrive_native(sqs, dlq_arn, rate=rate)
                print_success(
                    f"{label}: started native move task "
                    f"(handle: {result['task_handle'][:24]}…)"
                )
            else:
                depth = _get_queue_depth(sqs, dlq_url)
                result = _redrive_manual(
                    sqs,
                    dlq_url,
                    source_url,
                    source_type=source_type,
                    message_type=message_type,
                    limit=limit,
                    dry_run=dry_run,
                    rate=rate,
                    depth=depth,
                    run_id=run_id,
                )
                if dry_run:
                    print_success(
                        f"{label}: DRY RUN — {result['matched']} message(s) would "
                        f"move, {result['skipped']} skipped by filter"
                    )
                else:
                    print_success(
                        f"{label}: redrove {result['redriven']} message(s), "
                        f"{result['skipped']} skipped, {result['failed']} failed"
                    )
        except Exception as e:
            print_error(f"Failed to redrive {queue_name}: {e}")
            continue

    redrive_id_note = f"  Redrive ID: {run_id}" if run_id else ""
    print(
        f"\n{_bold('Done.')} Redrive run complete across "
        f"{len(selected_queues)} queue(s).{redrive_id_note}\n"
    )


def main() -> None:
    """
    Interactive DLQ tool entry point.

    Prompts for environment, queue selection, and an action (inspect or redrive),
    then runs the chosen flow against the selected DLQs.
    """
    # Guard against missing boto3 early so the error message is friendly
    try:
        import boto3  # type: ignore[import-untyped]
    except ImportError:
        print_error("boto3 is not installed.  Run: cd scripts && uv add boto3")
        sys.exit(1)

    print(f"\n{_bold('=== Matik DLQ Inspector ===')}")
    print(
        "This tool inspects (read-only) or redrives Matik SQS dead letter queues.\n"
        "Inspect output is saved to dlq-output/<env>/ relative to the run directory."
    )

    # Step 1: Choose environment
    env = _ask_env()

    # Production safety gate
    if not _confirm_production(env):
        print_info("Aborted.")
        sys.exit(0)

    # Step 2: Choose queues
    selected_queues = _ask_queues(env)

    # Step 3: Choose action
    action = _ask_action()

    # Step 4: Connect to AWS
    print_info(f"Connecting to SQS in {REGION}…")
    try:
        sqs = boto3.client("sqs", region_name=REGION)
        # Quick sanity check — listing queues doesn't require special perms and
        # validates that credentials are working before we start.
        sqs.list_queues(MaxResults=1)
    except Exception as e:
        print_error(f"Failed to connect to AWS: {e}")
        print_info(
            "Check that your AWS credentials are configured.  "
            "Set AWS_PROFILE if you need a specific profile."
        )
        sys.exit(1)

    # Step 5: Run the chosen flow
    if action == "redrive":
        _run_redrive(sqs, env, selected_queues)
    else:
        _run_inspect(sqs, env, selected_queues)
