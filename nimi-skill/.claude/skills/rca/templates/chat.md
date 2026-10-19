# Chat message (the `RCA_CHAT` block)

The one message on-call sees in chat. The full report is stored separately; this message must stand on its
own and be readable in under a minute.

You write **only the content, as JSON with fixed keys**. NIMI owns the layout: it adds the section names, the
order, bullets, numbering and the header, so never write headings, section names, bold labels or Markdown
layout inside a value.

## Keys

```json
{
  "title": "what is failing, where — one short line",
  "context": "one line: the main subject(s) and the environment or owner, e.g. `orders` | load job: order-loader, prod",
  "tldr": "2–3 short sentences: what is happening, the likely cause, whether it fixes itself, the one thing to do",
  "cause": {"certainty": "confirmed | likely",
            "points": ["the cause and the key facts that support it, one point per fact"]},
  "verify": [{"where": "where this step runs, e.g. a database, Grafana → Loki, kubectl; null if obvious",
              "text": "a read-only check and what the result means",
              "command": "exact command or query, or null",
              "outcomes": ["what each possible result means: result → meaning / what to do"]}],
  "now": ["what to do right now to limit impact, or that nothing is needed and why; when to escalate"],
  "do_not": ["a tempting action that would make things worse, and why"],
  "fixes": [{"owner": "who would make the fix, e.g. Source job (owning team)", "items": ["the real fix first"]}],
  "also": ["missing/outdated runbooks or docs; monitoring or alerts to add or change"],
  "related": [{"ref": "msg:<message_id> | rca:<result_id>", "label": "what it is, time UTC", "why": "why it matters, a few words"}]
}
```

- `title`, `tldr` and `cause` are required. Leave out (or leave empty) any other key that doesn't apply: no
  `do_not` if nothing tempting is harmful, no `verify` if there is nothing useful to check.
- `title`: no "RCA:" prefix (NIMI adds it), at most ~100 characters.
- `cause.certainty`: `confirmed` only with direct evidence; otherwise `likely`.
- `verify`: one entry per step, each with its own `where`, because checks often span systems (a DB query,
  a Loki query in Grafana, a `kubectl` command). Give the command or query exactly, in `command`. When the
  result decides what to do next, list each possible result in `outcomes` ("identical rows → ingested twice");
  NIMI shows them under the step.
- `do_not` items are the action itself, because NIMI shows them under "Do NOT": write "Restart the worker for
  this alone: …", not "Do not restart …".
- `related`: at most 3 messages or earlier RCAs from the history that clearly support the cause. This is the
  only place for earlier RCAs.
- Every value is plain text. Inside text you may use `` `code` `` and citations (below); nothing else.

## Length budget

The whole message must stay **under ~2,500 characters** of text in total. Fit it by editing, not by cutting:
merge points, summarise, drop the least useful steps, fixes and suggestions, prefer one strong fact over
three weak ones. Typical sizes: TL;DR 2–3 sentences; 2–4 items per list.

## Writing rules

- **Simple words, short sentences.** Write for a tired on-call engineer: plain English, no jargon you don't
  explain, no filler ("it is worth noting", "in conclusion"). Prefer "the job keeps a transaction open for
  hours" over "long-lived transactional contention".
- **Say how sure you are.** Never state a guess as fact.
- **No internal chatter.** Don't mention earlier RCAs, NIMI or how you investigated anywhere except `related`
  ("this agrees with RCA #2", "the subjects assigned to this RCA"). On-call needs the finding, not its history.
  An earlier RCA that really helps goes under `related` as a link, nowhere else.
- **Only what helps act.** Leave out the investigation story, file-by-file evidence and timelines; keep the
  one or two facts that make the cause believable (counts, times in UTC, sizes, error text).
- **Verify against the live system, not your evidence.** Steps to verify are what on-call checks in the real
  system to confirm the cause: the logs of the system the alert is about (where the project file says they are, e.g.
  `kubectl logs`, a log viewer; `<log viewer>` if unknown), its database or config, dashboards. Don't repeat
  evidence you already cited, and don't send people to chat spaces: only some errors reach chat, and that
  copy is NIMI's evidence, not the source.
- **Verification steps are read-only and safe** (queries, `kubectl get/describe/logs`, dashboards). Anything
  that changes something goes under `now` or `fixes` as a suggestion, never as a step.
- **Don't invent specifics.** Hosts, namespaces, contacts, thresholds or channel names you don't know are
  written as placeholders: `<primary DB host>`, `<owning team>`, `<X>%`.
- **No sensitive values** (same rule as the report): describe secrets and personal data, never copy them.
- **Code formatting only for what someone would copy or type:** a table, column, config key, command or
  query. Not for chat channel names, error messages (quote them instead), team names or plain words.
- **Channels by their real label.** Refer to a chat channel only by a `channel_label` you saw in query
  results, written exactly as it appears there; never invent or guess one.
- **Cite, don't link.** Inside text, point at a history message with `[short label](msg:<message_id>)`
  (`message_id` from `skill_messages`) and at code with `[file.py:88](code:<project>@<commit>/<path in repo>#L88)`.
  NIMI turns these into links. Never write URLs or absolute local paths, and only cite IDs and lines
  you actually saw. Labels describe the thing — what it is and when, e.g. "payment timeout log, Mar 3 14:20
  UTC" — never just its ID ("msg 1234" means nothing to readers).

## Valid JSON

Write the JSON object only (no code fence, no comments, no trailing commas), with double quotes and `\"`
for quotes inside text. It must parse; if it doesn't, NIMI posts the report's summary instead.

## Example (fictional)

```
RCA_CHAT
{
  "title": "Nightly order load failed – warehouse (prod)",
  "context": "`orders` | load job: order-loader, prod",
  "tldr": "The 02:00 UTC load failed because the warehouse API returned errors for 4 minutes. The scheduler retries at 02:30, so it should fix itself. On-call: check the retry succeeded.",
  "cause": {"certainty": "likely",
            "points": ["All 37 errors are `503 Service Unavailable` between 02:10 and 02:14 UTC, then nothing: a short outage on the warehouse side.",
                       "No code or config changed this week; the loader fails the whole run on any 5xx ([loader.py:88](code:shop@3f2a9c1/services/order-loader/loader.py#L88))."]},
  "verify": [{"where": "Scheduler dashboard", "text": "Check the 02:30 attempt.", "command": null,
              "outcomes": ["succeeded → nothing else to do", "503 again → the outage is ongoing; check the provider's status page"]},
             {"where": "Orders DB (read replica)", "text": "Count today's loaded orders; 0 means the retry hasn't run yet.", "command": "SELECT count(*) FROM orders WHERE loaded_at >= current_date;"}],
  "now": ["Nothing if the retry succeeded. If two retries fail, tell `<owning team on-call>`."],
  "do_not": ["Re-run the full backfill: it duplicates rows."],
  "fixes": [{"owner": "order-loader (owning team)", "items": ["Retry 5xx responses inside the job with backoff, so a 4-minute blip doesn't fail the whole load."]}],
  "also": ["The runbook has no section for this alert: add \"check the next retry first\".",
           "Alert only after two failed attempts in a row."],
  "related": [{"ref": "msg:1235", "label": "warehouse API 503 log, 02:10 UTC", "why": "first error, from the logs channel"}]
}
END_RCA_CHAT
```
