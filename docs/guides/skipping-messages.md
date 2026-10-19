# Messages that don't need an RCA

Only **new, top-level bot messages** in channels registered with `--rca` are considered at all. Human
messages, thread replies and backfilled history are stored but never analyzed. Of the rest, several
layers make sure RCAs (and their cost) go to messages that report a real problem.

| Layer | Who decides | Cost | Outcome |
|---|---|---|---|
| Age: older than `NIMI_STALE_HOURS` | code | none | `skipped` ("stale: …") |
| Sender allowlist `NIMI_ALERT_SENDERS` (if set) | code | none | `skipped` |
| **Exclusion rules** | code | none | `skipped` ("excluded by rule #id: reason") |
| OK / resolved, **informational**, **test** messages | triage model | one cheap call | `not_applicable` |

Excluded and declined messages are still stored and still visible to the skill's history.

## Exclusion rules

A rule matches a message when **every** field it sets matches; unset fields match anything.

| Option | Matches |
|---|---|
| `--like <message>` | the same **signature** as that stored message (internal or provider message ID) |
| `--signature <key>` | exact signature key |
| `--channel <label>` | the channel |
| `--sender <id>` | exact sender ID, e.g. `users/123` (see the note below) |
| `--title <text>` | case-insensitive substring of the parsed title (or the first line of the text) |
| `--status <status>` | parsed status, e.g. `Alerting` (case-insensitive) |
| `--text <text>` | case-insensitive substring of the message's full text, e.g. a service or host name that the parser keeps out of the title |

A signature is the parser's coarse "kind of message": varying parts such as numbers, IDs, timestamps and
masked subjects are removed, so all occurrences of the same alert share it. **The service matches it in
code; no model is involved.** Because it is coarse, `--like` can cover more than one specific alert (e.g.
the same alert type for every table), so `exclude add` always previews the signature and the stored
messages it would have matched, and asks before saving. Narrow it with the other options if it's too
broad.

```bash
# "stop doing RCAs for messages like this one"
.venv/bin/python -m nimi_service exclude add --like spaces/AAAA…/messages/… \
    --reason "Ticket reminder: new tickets waiting to be claimed; no failure"

# temporary mute while a known issue is being fixed
.venv/bin/python -m nimi_service exclude add --channel alerts --title "disk usage" \
    --reason "infra resizing volumes this week" --expires 7d

# every memory alert from the batch hosts, whatever the exact host
.venv/bin/python -m nimi_service exclude add --text "batch-worker" --title "memory usage" \
    --reason "batch hosts: memory peaks during nightly jobs are expected"

.venv/bin/python -m nimi_service exclude              # active rules with hits and last hit
.venv/bin/python -m nimi_service exclude list --all   # include disabled / expired
.venv/bin/python -m nimi_service exclude disable 3
.venv/bin/python -m nimi_service exclude enable 3
```

- A reason is required; `--expires` takes `7d`, `12h`, `30m` or an ISO time.
- Rules also apply to requests already waiting for triage when the rule is added (for example a backlog
  after a usage-limit pause).
- Messages without a signature (unparsed) can't be used with `--like`; use the other options.
- **Google Chat incoming webhooks share one sender ID**: messages from different webhooks (e.g. "Alerts
  test", "Grafana", every log webhook) all come from the same `users/…` ID, so `--sender` and
  `NIMI_ALERT_SENDERS` can't tell them apart. Use `--like`, `--title` or `--channel` instead.
- After a parser change, `reparse` warns about rules whose signature no longer exists; re-create them with
  `--like`.
- Review hit counts now and then: a rule with an unexpectedly high count may be too broad; one with no
  hits for months may be obsolete.

## What triage declines

The triage model (no tools; see [Triage](../architecture/triage.md)) returns `not_applicable` for:

- **OK / resolved** messages ("[OK] …", "changed status to OK");
- **informational** messages that report no failure: ticket/workflow reminders, periodic summaries,
  "job finished", deploy or maintenance notices (reason starts with `informational:`);
- **test** messages: notification-channel tests (e.g. Grafana `TestAlert`), "test alert, please ignore",
  `[TEST] …` (reason starts with `test:`).

Safeguards:

- A message that reports an error, a missed threshold, missing or late data, or degraded service is never
  informational, however routine.
- A real failure that merely *mentions* test, staging or QA (a table, job or environment name) is not a
  test.
- **Sender, webhook, bot and space names are never evidence**: a webhook called "Alerts test" that carries
  production alerts still gets RCAs.
- When unsure, triage chooses an RCA.

Every decision's reason is stored (`rca_requests.triage_reason`). If triage declines something it
shouldn't, tell us; if it keeps analyzing something harmless, add an exclusion rule.
