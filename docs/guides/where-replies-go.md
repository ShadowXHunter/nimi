# Replies

## The message

Each RCA is **one** chat message; the full report stays in the database. The skill fills in the
message's fields in simple words, as JSON (see `skills/rca/templates/chat.md` and the
[output contract](../reference/output-contract.md#full-report-and-chat-message)); NIMI lays them out in a fixed
order with fixed section names and adds the first line (`👁️ NIMI — ` + the disclaimer, in italics),
the `RCA:` title line, the status line and the rating line:

```
👁️ NIMI — Automated RCA, not verified. Based on code and docs that may differ from production.

RCA: Nightly order load failed – warehouse (prod)
Confidence: medium · Origin: upstream · Self-healing: yes

TL;DR: The 02:00 UTC load failed because the warehouse API returned errors for 4 minutes. The scheduler
retries at 02:30, so it should fix itself. On-call: check the retry succeeded.

Cause (likely, to be confirmed)
• …

Steps to verify
1. …

Now
• …

Do NOT
• Re-run the full backfill: it duplicates rows.

Permanent fixes
order-loader (owning team)
• …

Also
• The runbook has no section for this alert: add "check the next retry first".

Related
• warehouse API 503 log, 02:10 UTC (link to the original message) — first error, from the logs channel

Rate it: 👍 correct · 🤔 partly right · 👎 wrong
```

- **Reuse** (same ongoing incident as an earlier RCA): "Same as an earlier RCA" with, per earlier RCA, the
  subjects it covers, a link to it (labelled by its title and time, never a database ID) and its TL;DR. **Partly reused** RCAs show the same lines under
  "Already analyzed:".
- **Related** lists up to 3 messages from the history (e.g. the error log in another channel that explains
  an alert) or earlier RCAs, linked to the original messages. The skill cites them by ID and NIMI makes the
  links, so only real bot messages and real RCAs become links
  ([citations](../reference/output-contract.md#citations)). History, and so citations, is only available
  when the service runs the skill.
- **Usage limit reached:** a one-line notice, no promise of a later RCA.
- The first line's disclaimer is `NIMI_REPLY_DISCLAIMER`. Messages stay under `NIMI_POST_MAX_CHARS`; only the body is
  shortened if needed, never the header or footer.

## Where replies go

With `NIMI_POST_ENABLED=true`, NIMI posts each RCA (and reuse decisions and usage-limit notices) to one or
both **destinations**, chosen with `NIMI_POST_TARGET`:

| `NIMI_POST_TARGET` | Posts go to |
|---|---|
| `thread` (default) | A reply in the alert's own thread, posted as the user whose token NIMI uses. |
| `webhook` | A separate space, through its incoming webhook (`NIMI_POST_WEBHOOK`). |
| `both` | Both of the above. |

### Posting to a separate space

```bash
# nimi-service/.env (or ~/.config/nimi/service.env)
NIMI_POST_ENABLED=true
NIMI_POST_TARGET=webhook
NIMI_POST_WEBHOOK=https://chat.googleapis.com/v1/spaces/<space>/messages?key=<key>&token=<token>
```

Create the webhook in the destination space (Apps & integrations → Webhooks). The URL contains a secret: it
is masked by `config`, never logged, and errors never include it. `run` refuses to start if the target
includes `webhook` but the URL is missing or isn't a Google Chat webhook URL.

Webhook posts stay understandable outside the alert's thread:

- the line under the header links back to the **original message**: `🔔 Original message in team-alerts: Link`;
- each alert gets its own thread in the destination space: its first post is a new top-level message, and
  later posts for the same alert (a re-run, a reuse decision, a notice) reply in that thread. Different
  alerts never share a thread;
- links to earlier RCAs (in reuse replies) point to the earlier post in the same destination when there is
  one.

### Several people, one alert space

Each person can run their own NIMI (own database, settings and Claude account) against the same alert
space. To avoid duplicate replies in the alert threads, let **one** instance use `thread` and the others
`webhook` to their own (e.g. personal) spaces. Each instance triages and runs RCAs independently, so each
pays for its own.

### Feedback

👍 / 🤔 / 👎 work in both destinations. For webhook posts, NIMI lists the destination space once per
reaction interval and fetches reactions only for posts that have (or had) any, so the token's account must
be able to read that space (it usually is the user's own space). If it can't, NIMI raises one low-severity
red flag and webhook feedback is skipped; posting still works.

### Safety

- If the destination space is also a registered channel, NIMI ignores its own posts there (by message ID
  and by their `👁️ NIMI —` first line), so it never analyzes its own RCAs.
- Each destination is tracked separately: if posting to one fails (a red flag is raised), the outbox
  retries just that one on the next poll, within `NIMI_STALE_HOURS`.
- Posts are limited to `NIMI_POST_MAX_CHARS`, with room kept for the source line.
