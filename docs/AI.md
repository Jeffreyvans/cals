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
| `AI_COST_PER_MILLION_INPUT` | Operator's input-token rate for the usage estimate; `0` when unset. |
| `AI_COST_PER_MILLION_OUTPUT` | Operator's output-token rate for the usage estimate; `0` when unset. |

No model name or key is hard-coded. If the key or the selected model name is
missing, the provider raises a clear configuration error and no network request
is made. Slow, down or rejected services produce short, safe user messages
such as "The AI service took too long to answer. Nothing was changed; please
try again." Provider exception details are never shown to a user.

`ai/service.py` is the only supported entry point. `ask_ai()` catches provider
failures, bounds the response size and returns an `AIResult` that says whether
the call succeeded. It does not import Celery or schedule background work.
Views must use `ask_ai_for_user()`, which runs every control below before it
calls `ask_ai()`. Nothing reaches the provider until all of them pass.

## Controls

Four separate gates must all be open. A missing or failed gate returns a short
message and writes a zero-token `blocked` log row; no provider call is made.

1. **Platform kill switch.** A single platform setting starts closed. Only
   platform staff with `platform.manage_settings` can open it at
   `/platform/ai/`, and a one-click "close everything now" button remains
   available. Closing it stops every institution and feature immediately,
   whatever their own switches say.
2. **Institution switch.** Each of the five features has an
   `AIFeatureSetting` row per institution. A missing row means **off**. An
   institution administrator (permission `ai.manage`) switches a feature on or
   off at `/ai/settings/`; nobody else can change this.
3. **Permission and active consent.** A person needs `ai.use` and an active,
   unwithdrawn acceptance of the disclosure at the current consent version.
   Consent is per person and per feature; "all features" is also available.
4. **Usage limits.** Four limits are stored per institution and feature:
   institution calls per day, institution calls per month, user calls per day
   and user calls per month. Defaults are conservative (100, 1000, 20 and
   200). Every attempt counts, including blocked attempts, so opening a limit
   cannot be bypassed by repeatedly calling a disabled feature.

A configuration or provider failure is not a control failure: the person sees
a safe "temporarily unavailable" or "not configured" message and the failed
call is logged with zero tokens.

## What a school must agree to

Before first use, `/ai/consent/` shows the provider name (`Anthropic Claude`),
the feature being accepted, the exact categories sent to it, what is recorded
locally, and the consent version. Accepting stores a snapshot of those
categories, the purpose, the named person who accepted, the time and the
request IP. Withdrawal sets `withdrawn_at` rather than deleting the old record,
and takes effect at once. The screen can be revisited at any time.

The local record for every call (`AIUsageLog`) contains the institution, user,
feature, status (success, provider failure or blocked), tier, model name,
token counts, a cost estimate, the time and a short **redacted** prompt
summary. It never stores a full prompt or answer. The `/ai/usage/` page shows
today's and this month's counts beside the limits; administrators can see all
rows for their own institution, while other users see only their own rows.

The cost estimate is informational. Set `AI_COST_PER_MILLION_INPUT` and
`AI_COST_PER_MILLION_OUTPUT` to the operator's own rates; both default to `0`,
which produces a zero estimate rather than inventing a price.

## Data sent by each feature

The features themselves are added in the next part. The disclosures are
already stored on the consent record and shown on the consent screen:

| Feature | Categories disclosed as shared |
| --- | --- |
| Help assistant | The redacted question and the titles of in-app guides selected locally as possible sources. |
| Draft writing | Redacted writing instructions and pasted draft text, plus the document type (report-card comment, announcement, fee reminder or letter). |
| Translation | The redacted text the person asks to translate. |
| Dashboard insights | Only labelled database totals, percentages and currency amounts; no names, student records, contact details or record-level details. |
| Natural-language search | The redacted request and the fixed list of field labels it may choose from; no SQL, record values or documents. |

"All features" discloses only the minimum redacted text each enabled feature
needs, and states that health and counselling information and records from
another school are never eligible.

## Privacy enforcement

`ask_ai_for_user()` redacts the system and user prompts **before** the provider
sees them. `ai/redaction.py` removes names explicitly supplied by the caller,
email addresses, URLs, phone numbers, ID-like numbers, address lines and
labelled personal fields. The same redacted text is used for the short usage
summary. The patterns are conservative: an over-reduced prompt is safer than a
leaked detail.

After redaction, `ai/privacy.py` checks the exact text about to be sent:

- Any mention of health, medical treatment, medication, diagnosis, a clinic,
  counselling, therapy, a case/session note or related sensitive terms stops
  the call. It does not matter whether the mention is in the user prompt or in
  a system prompt assembled by a feature.
- Any other institution's name or slug appearing in the prompt stops the call.
  A caller cannot choose another institution by passing it to
  `ask_ai_for_user()` either: the access check refuses the request, and the
  blocked row is written only against the signed-in person's own school.

A privacy refusal writes a zero-token `blocked` row with a generic summary such
as "Blocked before sending; nothing was shared." It never copies the refused
text into the log. This means a question such as "how do I open the health
module?" is also refused; that is intentional for an optional assistant.

Tests in `ai/tests/test_privacy.py` prove that names, ID numbers, phone
numbers, emails and addresses are absent from the provider request; that
health and counselling prompts never reach a provider; that another school's
name, slug or institution object is refused; and that the usage log holds no
full personal content.
