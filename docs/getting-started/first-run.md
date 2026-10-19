# First run

Assumes [Installation](installation.md) is done. All commands run from `nimi-service/`.

## 1. Register channels

```bash
.venv/bin/python -m nimi_service add-channel gchat AAAAxxxxxxx alerts --rca --display-name team-alerts
.venv/bin/python -m nimi_service add-channel gchat AAAAyyyyyyy app-logs          # logs: history/context only
.venv/bin/python -m nimi_service channels
```

- The channel ID is the space ID (`AAAA…` or `spaces/AAAA…`); the label is your short name for it.
- `--rca` enables RCAs for the channel. Without it, messages are still ingested and visible to the skill
  as history (e.g. register a logs space without `--rca` to use its logs as context for alert RCAs), but
  never analyzed themselves.
- Running `add-channel` again for a registered channel updates it in place: `--rca` / `--no-rca` switch
  RCAs on or off (from new messages on); options you leave out keep their current values.
- `--backfill-days N` sets how much history the first poll pulls (default `NIMI_BACKFILL_DAYS`, 90).

## 2. Ingest once (backfill)

```bash
.venv/bin/python -m nimi_service ingest-once
.venv/bin/python -m nimi_service status
```

The first poll of each channel pulls its history. **Backfilled messages never trigger RCAs**: they are
history for triage and the skill. Later polls fetch only new messages.

## 3. Exclude known noise (optional)

Look at what arrives and exclude messages that never need an RCA:

```bash
.venv/bin/python -m nimi_service exclude add --like <message id> --reason "ticket reminder, no failure"
```

See [Messages that don't need an RCA](../guides/skipping-messages.md).

## 4. Try an RCA on one message

```bash
.venv/bin/python -m nimi_service rca <provider message id>     # e.g. spaces/AAAA…/messages/…
```

This runs triage and the skill now and prints the result. Nothing is posted while
`NIMI_POST_ENABLED=false`.

## 5. Run the service

```bash
.venv/bin/python -m nimi_service run
```

Every `NIMI_POLL_SECONDS` (60) it recovers stuck runs, ingests new messages, triages, runs queued RCAs (up
to `NIMI_MAX_PARALLEL`), posts replies (if enabled), syncs reactions, and retries red-flag notifications.
Stop it with Ctrl-C; running RCAs finish first. After a restart it continues where it stopped
([Operating the service](../guides/operating.md#restarts-and-downtime)).

## 6. Turn on posting

When you are happy with the stored results, set `NIMI_POST_ENABLED=true` and restart. Replies go into the
alert's thread by default; to send them to a separate space instead (or as well), see
[Where replies go](../guides/where-replies-go.md).
