# Optional AI assistance in Vanpere

All AI assistance in Vanpere is optional and off by default. Nothing calls an
external AI service until the platform operator configures a key and model,
the platform kill switch is opened, an institution administrator switches on
the individual feature, and the person using it has accepted the consent
screen. Those controls are described below as they are added.

The guiding rules are:

- AI calls run on the server only. Browser code never holds a key and never
  calls the provider directly.
- The model is a text generator, not an agent. It has no tool, database, file,
  email or messaging access. Data is passed to it as untrusted text.
- Anything sent to a provider is redacted first, and health and counselling
  information is never eligible for AI processing.
- AI output is never trusted as an instruction, SQL, a link or a command.
- An AI draft is not a saved record or a sent message until a named person
  reviews and explicitly approves it.

## Provider layer

`ai/providers.py` defines a small provider interface (`complete()` taking a
system prompt, a user prompt, a tier and a token cap) and two implementations:

| Implementation | Purpose |
| --- | --- |
| `AnthropicProvider` | Production implementation using the Anthropic Messages API over HTTPS with Python's standard library. |
| `MockProvider` | Deterministic in-process implementation for automated tests. It records requests and can return queued answers or failures. |

Configuration is read from the environment at call time:

| Variable | Meaning |
| --- | --- |
| `AI_API_KEY` | Provider key. Required. Never written to the database, logs, templates or error messages. |
| `AI_MODEL_FAST` | Model used for short, high-volume tasks. Required for the `fast` tier. |
| `AI_MODEL_SMART` | Model used for harder writing tasks. Required for the `smart` tier. |
| `AI_API_URL` | Optional endpoint override, useful for a self-hosted gateway. |
| `AI_TIMEOUT_SECONDS` | Request timeout, 15 seconds by default. |
| `AI_PROVIDER` | `anthropic` in production; `mock` only in automated tests. |

No model name or key is hard-coded. If the key or the selected model name is
missing, the provider raises a clear configuration error and no network request
is made. Slow, down or rejected services produce short, safe user messages
such as "The AI service took too long to answer. Nothing was changed; please
try again." Provider exception details are never shown to a user.

`ai/service.py` is the only supported entry point. `ask_ai()` catches provider
failures, bounds the response size and returns an `AIResult` that says whether
the call succeeded. It does not import Celery or schedule background work.

## What a school must agree to

The consent screen will name the provider, the features in use, the exact
categories of data shared, what is recorded locally, who accepted, and when.
This section will be completed with the controls in the next part.

## Data sent by each feature

Detailed per-feature tables will be added with each feature. The current rule
is that only the minimum redacted text needed for that feature is sent; record
ids, tokens, counts, names, contact details, addresses, health data and
counselling data are not sent as free text.
