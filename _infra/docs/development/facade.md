# Overview

Matik has two LLM client options, both routing through [LLM Fusion Hub](https://developers.a.musta.ch/docs/default/component/llm-fusion-hub/quick-start?utm_source=glean):

- **Facade** (`common/clients/facade_client.py`) — Azure/OpenAI proxy → GPT models (e.g., `gpt-5`)
- **Bedrock** (`common/clients/bedrock_client.py`) — AWS Bedrock proxy → Claude, Llama, Mistral, and other Bedrock-hosted models

Both clients share the same authentication pattern, message helper functions (`FacadeMessage`, `facade_system_message`, etc.), and retry utilities.

**Who calls these:** the **Enricher** service (the sole LLM-summary path — see
[Enricher Design](../architecture/enricher-design.md)) and the **evals** harness.
Historians and Chronicler do not call Facade directly; they forward enrichment
requests to the Enricher's SQS queue.

# Facade Client (Azure/OpenAI → GPT)

## Dedicated Model Deployments

> ⚠️ **Important: Use Matik's Dedicated Model Deployments**
>
> Do NOT use the shared `gpt-5` deployment. Our high usage causes rate limiting errors for other teams:
> ```
> LLMFusionHubError: The system is currently experiencing high demand and cannot process your request. Your request exceeds the maximum usage size allowed during peak load. Please retry after 7 seconds. For improved latency reliability, consider switching to Provisioned Throughput.
> ```
>
> Use Matik's dedicated deployments instead:
>
> | Environment | Model | Resource Bucket |
> |-------------|-------|-----------------|
> | local | `matik-sandbox-gpt-5` | `production` |
> | sandbox | `matik-sandbox-gpt-5` | `production` |
> | staging | `matik-staging-gpt-5` | `production` |
> | production | `matik-production-gpt-5` | `production` |

## IAP Auth Setup (Local Development)

For local development, you need an IAP token for both Facade and Bedrock clients:

```bash
airtool install iap-auth
iap-auth https://llm-fusion-hub.a.musta.ch
export IAP_TOKEN="<token>"
```

In Kubernetes (staging/production), AirMesh handles authentication automatically — no IAP token needed.

## Load Configuration

Add `local-configs/facade-config.yml` to your service's config loading. Example config:

```yaml
facade:
  base_url: https://llm-fusion-hub.a.musta.ch/api/v2/proxy/azure/oai
  resource_bucket: production
  default_model: matik-sandbox-gpt-5
  api_version: 2024-12-01-preview
```

## Create the Client

```python
from common.config import load_config
from common.clients.facade_client import create_facade_client

config = load_config("config/matik-service-config.yml")
client = create_facade_client(
    facade_config=config.facade,
    common_config=config.common,
)
```

## Send Messages

Use the helper functions to create messages with different roles:

- `facade_system_message(content)` — Sets the AI's behavior/instructions
- `facade_user_message(content)` — Questions or prompts from the user
- `facade_assistant_message(content)` — Previous AI responses (for conversation context)

### Simple Query

```python
from common.clients.facade_client import facade_system_message, facade_user_message

messages = [
    facade_system_message("You are a helpful assistant."),
    facade_user_message("What is the capital of France?"),
]

response = await client.send_message("matik-sandbox-gpt-5", messages)
print(response)
```

### With Retry Logic

Use `send_message_with_retry` for automatic retries with exponential backoff (default: 3 retries with 10s, 20s, 40s delays):

```python
messages = [
    facade_system_message("You are an incident analyst."),
    facade_user_message("Summarize this incident: ..."),
]

response = await client.send_message_with_retry("matik-sandbox-gpt-5", messages)
```

### Conversation with Context

```python
messages = [
    facade_system_message("You are an incident analyst."),
    facade_user_message("What's the status of INC-123?"),
    facade_assistant_message("INC-123 is currently active."),
    facade_user_message("Who's working on it?"),
]

response = await client.send_message(None, messages)  # None uses default model
```

---

# Bedrock Client (AWS Bedrock → Claude, Llama, Mistral, etc.)

## Supported Models

The Bedrock Converse API supports any model available in your AWS account. Common cross-region inference profiles:

| Model | Model ID |
|-------|----------|
| Claude Sonnet 4 | `us.anthropic.claude-sonnet-4-20250514-v1:0` |
| Claude Haiku 3.5 | `us.anthropic.claude-haiku-3-5-20241022-v1:0` |
| Llama 3 8B | `us.meta.llama3-8b-instruct-v1:0` |
| Llama 3 70B | `us.meta.llama3-70b-instruct-v1:0` |

## IAP Auth Setup (Local Development)

Same IAP token setup as the Facade client — see above.

## Load Configuration

Add `local-configs/bedrock-config.yml` to your service's config loading. Example config:

```yaml
bedrock:
  base_url: https://llm-fusion-hub.a.musta.ch/api/v2/proxy/aws/bedrock
  region: us-west-2
  default_model: us.anthropic.claude-sonnet-4-20250514-v1:0
  max_tokens: 4096
```

## Create the Client

```python
from common.config import load_config
from common.clients.bedrock_client import create_bedrock_client

config = load_config("config/matik-service-config.yml")
client = create_bedrock_client(
    bedrock_config=config.bedrock,
    common_config=config.common,
)
```

## Send Messages

The Bedrock client uses the same `FacadeMessage` helpers as the Facade client:

```python
from common.clients.facade_client import facade_system_message, facade_user_message

messages = [
    facade_system_message("You are a helpful assistant."),
    facade_user_message("What is the capital of France?"),
]

response = await client.send_message(None, messages)  # None uses default model
print(response)
```

System messages are automatically converted to Bedrock's `system` block format. User and assistant messages are converted to Bedrock's content list format.

### With Retry Logic

```python
messages = [
    facade_system_message("You are an incident analyst."),
    facade_user_message("Summarize this incident: ..."),
]

response = await client.send_message_with_retry(None, messages)
```

---

# LLM Call Optimization with Content Hashing

> **Where this lives now:** LLM summarization and the content-hashing that gates it
> live **only in the Enricher service** (`matik/enricher/`), which is the sole owner
> of the `FacadeClient`. Historians and Chronicler do **not** call Facade and do
> **not** hash-gate — they forward every record to the Enricher SQS queue
> unconditionally (see [Historian](historian.md) and
> [Enricher Design](../architecture/enricher-design.md)). The pattern below
> describes the Enricher's behavior, not the crawlers'.

When generating LLM summaries, use **content hashing** to avoid redundant API
calls: only re-summarize when the source content actually changed.

## Why This Matters

- **Cost Reduction**: LLM API calls are expensive
- **Latency Reduction**: skip unnecessary round-trips
- **Idempotent Processing**: safe to re-deliver the same enrichment request without
  duplicate LLM work

## The Pattern (owned by the Enricher)

Each enrichment request carries the source content plus the record's currently
stored summary hash. The Enricher:

1. **Generates a hash** (SHA256) of the request's content.
2. **Compares** it with the hash already stored on the record (delivered in the
   request's `hashes` envelope).
3. **Match** → skip the LLM; the existing summary is still valid.
4. **Mismatch** → call Facade for a fresh summary, then publish the new summary +
   hash back to Scribe, which upserts them onto the record's LLM columns.

The historian/Chronicler side is deliberately dumb: it publishes an
`EnrichmentRequest` (source content + entity id + current hashes) for every record
it sees. See `matik/enricher/handlers/enrichment_handler.py` for the hash-gating
implementation and `matik/common/clients/facade_client.py` for the client it calls.

## Database schema (the record's LLM columns)

The record model declares the LLM output + its gating hash as plain nullable
columns; the Enricher writes them (via Scribe), the base upsert never clobbers them
(they're `spec.llm_columns`, excluded from `update_columns`):

```python
from sqlmodel import Field, SQLModel
from sqlalchemy import Column, String, Text

class MyItemRecord(SQLModel, table=True):
    # ... base fields ...

    # LLM-generated summary (written by the Enricher).
    llm_summary: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    # SHA256 of the content the summary was generated from — gates re-summarization.
    summary_hash: str | None = Field(
        default=None, sa_column=Column(String(64), nullable=True)
    )
```

These field names must match the source's enrichment message (they become
`spec.llm_columns`) and the enricher `source_mappings` `output_field` / `hash_field`.
See [Onboarding a Data Source](onboarding-a-data-source.md) for how the two connect.

## Real-World Implementation

**Reference:** `matik/enricher/handlers/enrichment_handler.py` — the single place
that hash-gates and calls Facade. Per-source content keys, prompts, and hash columns
are pure YAML config (`local-configs/matik-enricher-config.yml`, `source_mappings`),
not code.
