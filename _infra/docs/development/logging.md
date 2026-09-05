# Logging

## Basic Usage

```python
from common.config import load_config
from common.utils import log_utils

# Load config
config = load_config("service-config.yaml")

# Configure logging with environment from config
log_utils.configure(environment=config.common.environment)

# Get a logger for your module
logger = log_utils.get_logger(__name__)

# Log messages with structured data
logger.info("processing incidents", count=42, status="active")
logger.warning("rate limit approaching", remaining=10)
logger.error("failed to fetch data", error_code=500)
```

## Exception Logging

Use `logger.exception()` to automatically include the full stack trace:

```python
try:
    do_something()
except Exception:
    logger.exception("operation failed")
```

## Adding Context

Bind additional context that appears in all subsequent logs:

```python
logger = log_utils.get_logger(__name__)
logger = logger.bind(user_id="456", team="ops")

logger.info("processing request")  # includes user_id and team
logger.info("request complete")    # includes user_id and team
```

## Task ID Tracking

For async operations and batch processing, use task ID tracking to correlate logs:

```python
# Generate a new task ID - pass __name__ to generate {service}-{uuid}
log_utils.generate_task_id(__name__)  # e.g., "historian-550e8400-e29b-41d4-a716-446655440000"

# Or use an incoming task ID (for distributed tracing)
from common.constants import TASK_ID_HEADER

incoming_id = request.headers.get(TASK_ID_HEADER)
if incoming_id:
    log_utils.set_task_id(incoming_id)
else:
    log_utils.generate_task_id(__name__)

# All subsequent logs automatically include task_id
logger.info("processing")  # includes task_id="historian-550e8400-..."

# Get current task ID (useful for propagating to downstream services)
current_id = log_utils.get_task_id()

# Send task ID to downstream service
headers = {TASK_ID_HEADER: log_utils.get_task_id()}
await client.post(url, headers=headers)

# Optional cleanup (contextvars auto-scope to async tasks)
log_utils.clear_task_id()
```

**Functions:**
- `generate_task_id(name)` - Generates a new task ID with format `{service}-{uuid}` and sets it in context
- `set_task_id(task_id)` - Sets an existing task ID in context (for distributed tracing)
- `get_task_id()` - Returns the current task ID (or None if not set)
- `clear_task_id()` - Clears the task ID from context

**Task ID Format:**
- Format: `{service}-{uuid}` (e.g., `historian-550e8400-e29b-41d4-a716-446655440000`)
- Service is extracted from the module name (e.g., `historian.main` → `historian`)
- UUID is auto-generated using `uuid.uuid4()`

**How it works:**
- Uses Python's `contextvars` for async-safe context propagation
- Task ID is added to logs by a processor (not bound to logger)
- Each async task/coroutine maintains its own task ID context
- Useful for tracing operations across multiple log lines and services

**Batch processing example:**

```python
async def process_batch(items: list):
    # Each batch gets its own task_id with service prefix
    log_utils.generate_task_id(__name__)  # e.g., "historian-{uuid}"
    logger.info("starting batch", item_count=len(items))

    for item in items:
        await process_item(item)  # All logs include this batch's task_id

    logger.info("batch complete")


async def run_historian():
    batches = [["a", "b"], ["c", "d"], ["e", "f"]]

    # Each concurrent batch has its own task_id context
    await asyncio.gather(*[
        process_batch(batch)
        for batch in batches
    ])
```

## Output Format

Output format is determined by `config.common.environment`:

| Environment | Format |
|-------------|--------|
| `local` | Human-readable colored console |
| `sandbox`, `staging`, `production` | Structured JSON |
| `unknown` | Structured JSON (default before configuration) |

**Default behavior:**
- Before `configure()` is called: `deployment="unknown"` (useful for error logs during failed config loading)
- After `configure()` with no environment: `deployment="sandbox"`

## Log Levels

Configure log level at startup:

```python
log_utils.configure(level="DEBUG", environment="production")
```

## Standard Pattern

```python
"""Service entry point."""

from common.config import load_config
from common.utils import log_utils

logger = log_utils.get_logger(__name__)


def main() -> int:
    try:
        config = load_config("matik-service-config.yaml")
    except Exception:
        # Logs with deployment="unknown" since configure() hasn't been called
        logger.exception("failed to load config")
        return 1

    log_utils.configure(
        level=config.common.log_level,
        environment=config.common.environment,
    )

    logger.info("job started")

    try:
        # do work
        logger.info("job completed successfully")
        return 0
    except Exception:
        logger.exception("job failed")
        return 1
```

**Note:** The logger is created at module level before `configure()` is called. This works because deployment is added dynamically at log time, not when the logger is created.
