# CLI

Run from `nimi-service/`:

```bash
.venv/bin/python -m nimi_service <command> [options]
```

## Database

| Command | What it does |
|---|---|
| `db upgrade` | Create or update the schema to the latest migration, then grant the skill reader its views. |
| `db revision "<message>"` | Draft a migration from model changes (review it before applying). |
| `db current` | The database's schema version vs the latest. |

## Channels and ingestion

| Command | What it does |
|---|---|
| `gchat-login --client-secret <file>` | One-time Google login (opens a browser); saves the Chat token to `NIMI_GCHAT_TOKEN_FILE`. |
| `gchat-spaces [--all]` | Your Google Chat spaces and their IDs (`--all`: include direct messages). |
| `add-channel <provider> <channel> <label> [--rca/--no-rca] [--backfill-days N] [--display-name X] [--notes X]` | Register a channel, or update a registered one in place (same row; watermark and messages kept). `--rca` / `--no-rca` turn RCAs on or off; options you leave out keep their current value (new channels: RCA off, `NIMI_BACKFILL_DAYS`). The label is always set. |
| `channels` | List registered channels with their watermark. |
| `ingest-once` | Poll all channels once (a channel's first poll backfills; backfill never triggers RCAs). |
| `backfill <channel> --days N` | Pull older history for a channel (label or ID); stored as backfill, no RCAs, watermark untouched; safe to re-run. |
| `parse-test [--text "…"] [--channel L] [--kind K] [--sender S] [--limit N]` | Show how the parser and [platform profiles](../guides/platform-profiles.md) read stored messages (or `--text`): matched layout, title, signature, and what `reparse` would change. Writes nothing. |
| `reparse` | Rebuild parses and signatures for all stored messages (after changing `parse.py` or a profile); moves RCA requests to the new signatures and warns about stale exclusion rules. |

## Running

| Command | What it does |
|---|---|
| `run` | The service loop: ingest, triage, RCA, post, reactions, red-flag retries. |
| `status` | Channels, message counts, request states, an active Claude pause. |
| `config` | Every setting, its effective value (secrets masked) and source. |

## RCAs

| Command | What it does |
|---|---|
| `rca <message> [--provider P] [--fresh] [--no-post]` | Triage and RCA one stored message now (provider message ID). `--fresh` always makes a new RCA: no reuse and no exclusion rules, and a secret in the message is flagged but doesn't hold it; triage still names the subjects (so later alerts can reuse it per subject) and its "not needed" verdict is overridden. `--no-post` stores the result but never posts it, not even later through a running service's outbox; read it with `show`. |
| `rca-text "<text>"` | RCA arbitrary text now (manual request: no triage, nothing posted). |
| `show <message\|request_id> [--provider P] [--chat \| --render] [--all]` | A stored RCA, without a new run: the request (status, triage decision, subjects, skip reason, posts) and the latest result's report. `--chat`: the chat message as posted (or as it would have been) instead of the report; `--render`: the chat message rendered now from the stored report, with the current layout and links and no length limit — shows sections that were left out to fit chat, and previews layout changes on past RCAs (not the posted copy); `--all`: every attempt (incl. errors and timeouts). For a reused request, shows the RCA it reused. |
| `release <request_id>` | Send a request held for critical sensitive data to triage anyway. |
| `resume` | Clear a Claude pause (usage limit, credit, login) now. |

## Exclusion rules

| Command | What it does |
|---|---|
| `exclude add [--like <message>] [--signature K] [--channel L] [--sender S] [--title T] [--status S] [--text X] --reason "…" [--expires 7d] [--yes]` | Add a rule; previews matching stored messages and asks before saving (`--yes` skips the question). |
| `exclude [list] [--all]` | Rules with hit counts; `--all` includes disabled and expired ones. |
| `exclude disable <id>` / `exclude enable <id>` | Turn a rule off or on. |

See [Messages that don't need an RCA](../guides/skipping-messages.md).

## Red flags

| Command | What it does |
|---|---|
| `red-flags [list] [--all]` | Open red flags (`--all`: include resolved and ignored). |
| `red-flags resolve <id> [--ignore] [--note "…"]` | Close a flag as resolved (or ignored). |

## Other scripts

| Script | What it does |
|---|---|
| `db/bootstrap.sh` | Create the database, the service role and the skill reader role on a local Postgres. |
| `dev/review_access.sh [grant]`, `dev/review_access.sh revoke` | Development only: create or remove a read-only role for reviewing stored bot messages and finished RCAs. |
| `bin/nimi-sql "<SQL>"` | Read-only history SQL; run by the skill during service RCAs. |
