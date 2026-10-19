# Red flags

NIMI doesn't try to redact free text. It **detects, records and alerts a human** instead. Flags are stored
in `red_flags` (deduplicated, and never containing a sensitive value) and sent to the admin's chat space
via `NIMI_ADMIN_WEBHOOK`.

## What raises a flag

| Source | Raises a flag when | Kind |
|---|---|---|
| scanner (code, before any model) | a high-confidence secret format is in the message: private keys, cloud/API keys, tokens, `password=…`, credentials in URLs. **Critical** hits are **held** from all models until `release`. | `sensitive_data` |
| triage model | it reports sensitive data in the message (types only) | `sensitive_data` |
| RCA skill | it reports sensitive data in the input, code, config, logs, docs or history (`sensitive_data:` in `RCA_FIELDS`) | `sensitive_data` |
| guard hook | it blocked a tool call (logged to `~/.local/state/nimi/guard-denials.jsonl`, matched to the run by session ID). Formatting slips are recorded as `low` without a notification. | `rule_violation` |
| service | triage failure, RCA error or timeout, posting / ingest / reaction failure, daily cap reached, Claude unavailable | `error` |

Repeats of the same problem bump `occurrences` on the open flag instead of notifying again.

## Replies are still posted

Sensitive data doesn't block replies (except critical secrets, which are held before any model sees them):

- the skill never writes sensitive values, only their type and location;
- outgoing text is masked for high-confidence formats as a backstop (and flagged if anything was masked);
- a visible note asks the alert owner to remove the data from the source and rotate exposed credentials.

## Handling flags

```bash
.venv/bin/python -m nimi_service red-flags                     # open flags
.venv/bin/python -m nimi_service red-flags --all               # include resolved / ignored
.venv/bin/python -m nimi_service red-flags resolve 12 --note "rotated the key"
.venv/bin/python -m nimi_service red-flags resolve 13 --ignore --note "test data"
.venv/bin/python -m nimi_service release 42                    # send a held request to triage anyway
```
