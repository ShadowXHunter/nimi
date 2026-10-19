# Data model

Postgres, managed with SQLAlchemy 2.0 models (`nimi_service/models.py`, the source of truth) and Alembic
migrations (`nimi-service/db/migrations/`). Rows use internal numeric IDs; provider IDs are stored as
`(provider, external_id)`, unique together. Sender types are normalized to `human` / `bot`.

## Tables

### Ingested data (what the provider returned)

| Table | Contents |
|---|---|
| `channels` | Registered channels: provider, external ID, label, `rca_enabled`, backfill days, ingest **watermark**, reaction check time. |
| `messages` | Top-level messages: sender, created time, `rendered_text` (readable form), `raw` provider payload, reaction summary, `is_backfill`. |
| `thread_messages` | Thread replies (including NIMI's own replies), same shape. |
| `reactions` | Individual reactions on messages. |

### Derived (rebuildable with `reparse`)

| Table | Contents |
|---|---|
| `signatures` | Coarse "kind of message" keys with a sample title and service. |
| `message_parses` | Per top-level message: parser version, kind (`alert` / `log` / …), title, status, service, signature. |

### Workflow

| Table | Contents |
|---|---|
| `rca_requests` | One per analyzed message (or manual text): status (see [lifecycle](../guides/operating.md#request-lifecycle)), `skip_reason`, triage decision / reason / output / model / cost, attempts, timestamps. |
| `rca_request_subjects` | Per request: subjects and their decision (`new` or `reused`, with the reused result). |
| `rca_results` | Skill runs: outcome, report, structured fields (origin, confidence, self-healing, repo, commit), cost, duration, model, session ID, skill version. |
| `rca_posts` | Messages posted (kind `rca` / `reuse` / `mixed` / `not_applicable` / `notice`), their **destination** (`thread` or `webhook`) and chat IDs. |
| `rca_post_reuse_links` | Which earlier results a reuse reply pointed to. |
| `rca_feedback` | Verdicts from reactions (`correct` / `partial` / `wrong`), soft-deleted when a reaction is removed. |
| `rca_exclusions` | [Exclusion rules](../guides/skipping-messages.md): match fields, reason, expiry, hit counts. |

### Operations

| Table | Contents |
|---|---|
| `red_flags` | [Red flags](../guides/red-flags.md): kind, severity, source, links to message/request/channel, dedupe key, occurrences, notification and resolution state. Never stores sensitive values. |
| `service_state` | Small persistent key/value state, e.g. the Claude pause. |

## Views

| View | Read by | Contents |
|---|---|---|
| `signature_stats` | service / humans | Occurrences, first and last seen per signature. |
| `rca_feedback_summary` | service / humans | Current feedback counts per result. |
| `skill_messages` | the skill (`nimi_skill_reader`) | Top-level **bot** messages from all channels with their parse and signature. No thread replies, human messages or raw payloads. |
| `skill_rcas` | the skill (`nimi_skill_reader`), development reviews (`nimi_readonly`) | Previous RCAs (outcomes `rca`, `not_applicable`, `insufficient_context`) with source message details, subjects and feedback counts. |
| `review_messages` | development reviews (`nimi_readonly`) | Bot messages, top-level **and** thread replies. |

The skill and review views use `security_barrier` so their filters apply before any caller condition.

## Database roles

| Role (default) | Used by | Access |
|---|---|---|
| `nimi_service` | the service | Owns the database and schema: read/write; the only role that can create objects. |
| `nimi_skill_reader` | the skill, via `nimi-sql` | `SELECT` on `skill_messages` and `skill_rcas` only. Read-only sessions, 5s statement timeout, 5 connections. Granted by `db upgrade`. |
| `nimi_readonly` | development reviews (optional, `dev/review_access.sh`) | `SELECT` on `review_messages` and `skill_rcas` only. Read-only, 30s timeout, 3 connections. |

The triage model has no database access: the service puts what it needs into the prompt.
