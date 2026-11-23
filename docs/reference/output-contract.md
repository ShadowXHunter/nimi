# Skill output contract

The service reads the skill's text output (`rca.py`, `parse_output`). The skill must follow this exactly.

## Early exits

A single line at the very start of the output, nothing else:

```
RCA_OUTCOME: not_applicable - <short reason, e.g. "resolved/OK notification">
RCA_OUTCOME: insufficient_context - <what is missing>
```

## Full report and chat message

The full report (layout in `skills/rca/templates/report.md`) is stored in the database. After it comes the
**chat message**, the one message NIMI posts, between two marker lines. The skill writes only its
**content, as one JSON object with fixed keys** (keys and writing rules in `skills/rca/templates/chat.md`);
NIMI owns the layout (`chat_message.py`):

```
RCA_CHAT
{
  "title": "Nightly order load failed – warehouse (prod)",
  "context": "`orders` | load job: order-loader, prod",
  "tldr": "2–3 short sentences",
  "cause": {"certainty": "confirmed | likely", "points": ["…"]},
  "verify": [{"where": "… or null", "text": "…", "command": "… or null", "outcomes": ["result → meaning"]}],
  "now": ["…"], "do_not": ["…"],
  "fixes": [{"owner": "…", "items": ["…"]}],
  "also": ["…"],
  "related": [{"ref": "msg:<id> | rca:<id>", "label": "…", "why": "…"}]
}
END_RCA_CHAT
```

- `title`, `tldr` and `cause` are required; empty or missing sections are left out of the message.
- NIMI renders the sections in a fixed order with fixed names (TL;DR inline; Cause, Steps to verify, Now,
  Do NOT, Permanent fixes, Also, Related as headings with bullets or numbered steps; each step starts with
  where it runs; commands as code blocks; possible results as sub-bullets under their step). The optional
  `context` line goes under the status line. The provider then converts the markup.
- Markdown headings inside values are removed, and sentences that mention an earlier RCA without citing it
  ("this agrees with RCA #2") are removed everywhere except Related. A sentence that cites one
  (`[RCA #12](rca:12)`) is kept; a citation labelled only by its ID is relabelled with that RCA's title and time.
- Values are plain text with `` `code` `` and citations only. A single text where a list is expected is
  treated as a one-item list. Unknown keys (e.g. `dont`, `permanent_fixes`) and `related` entries that aren't
  objects are ignored, and raise a low red flag.
- A `related` entry that points at the alert's own message is left out: on-call already sees the alert.
- **Length:** the skill keeps the whole message under ~2,500 characters by editing (summarising, merging,
  leaving out the least useful items). NIMI never trims text. Only if a message still can't fit
  `NIMI_POST_MAX_CHARS`, whole sections are left out (Related, then Also, Permanent fixes, Steps to verify)
  with a "(shortened; full report stored)" note.
- **Fallback:** if the block is missing, isn't valid JSON, or lacks a TL;DR or cause, the post uses the
  report's title and Summary.
- Fallbacks, ignored parts and shortening raise one low-severity [red flag](../guides/red-flags.md) per
  request, so a drifting template shows up.

### Citations

The skill never writes URLs. Inside text values, to point at a stored message (e.g. the error log behind an alert) it writes
`[label](msg:<message_id>)` with the `message_id` from `skill_messages`; for an earlier RCA,
`[label](rca:<result_id>)`; for code, `[label](code:<project key>@<commit>/<path in repo>#L<line>)`; to
attribute a claim to a person's comment, `[label](comment:<comment_id>)` (attribution only, never evidence).
Before posting, the service checks each one: cited **bot** messages become links
to the original message, cited RCAs link to their post (in the same destination when there is one),
cited code links to the file at that commit on the project's code host, cited comments link to their chat
message (active comments written in chat, on this alert or another one; a comment made with the CLI has no message and
keeps its label), and anything else — unknown IDs
or projects, human messages, unsafe paths, Markdown links to URLs — is reduced to its label. Absolute local
paths of registered checkouts are rewritten to `<project>: <path in repo>`. The stored
report keeps the citations, so it records which messages each RCA relied on.

### Verdicts on people's comments

If the input context gave the skill people's comments (see the [design](../design/comments.md)), the
chat block is followed by one JSON list between two marker lines: an entry for every comment given in full
about this alert, for each comment on another alert that applies (or that the cause relies on), plus any
background comment the cause relies on. Without comments, the block is left out.

```
RCA_COMMENTS
[{"id": 12, "verdict": "confirmed", "relied_on": true, "evidence": ["code:shop@3f2a9c1/services/export.py#L88", "msg:4411"]},
 {"id": 21, "verdict": "not_applicable", "relied_on": false, "evidence": ["tableD is in db_z"]}]
END_RCA_COMMENTS
```

| Key | Values |
|---|---|
| `verdict` | `confirmed`, `contradicted`, `unverifiable`, `not_applicable`. |
| `relied_on` | `true` if the cause relies on the comment. |
| `evidence` | Citations NIMI can check (`msg:<id>`, `code:<key>@<commit>/<path>#L<n>`), each optionally with a short note, or short notes. |

NIMI checks the block in code and stores it (`rca_comment_verdicts`):

- A comment given in full without a verdict is recorded as `unverifiable`.
- Comments on other alerts that were given need no verdict (most don't apply); a verdict given is stored.
- IDs that weren't given and aren't active comments on this alert are ignored.
- A `confirmed` with no citation that checks out (a stored bot message or a file of a registered project,
  anywhere in an evidence item) is recorded as `unverifiable` (`downgraded`). An earlier RCA (`rca:<id>`) never
  counts: it is an earlier answer, not evidence.
- Comments on RCAs rated 👎 or from `--no-post` runs are still given in full to the next RCA.
- If the cause relies on a comment that ends up `unverifiable`, NIMI caps the RCA: confidence at most
  `medium`, certainty at most `likely`, and the chat message says "The cause relies on a person's comment that
  NIMI couldn't verify."
- Problems raise one low red flag per request.

The output then ends with this block, as plain lines, one value per line, nothing after it:

```
RCA_FIELDS
origin: internal | upstream | undetermined
confidence: high | medium | low
self_healing: yes | no | unknown
repo: <project name in the registry, e.g. shop> | none
commit: <short sha analyzed> | none
sensitive_data: none | <where:type, where:type, …>
```

| Field | Values |
|---|---|
| `origin` | Where the trigger is, not who fixes it: `internal` (our code, config, infra, deploy is wrong or changed), `upstream` (a system we don't own, a third-party API/SDK, data; also when our code correctly rejects what it receives), `undetermined`. |
| `confidence` | `high` (direct evidence), `medium` (strong correlation), `low` (best hypothesis). |
| `self_healing` | Whether a higher level re-runs the failed work automatically. |
| `sensitive_data` | Types and locations only, never values. `where`: `input`, `code`, `config`, `logs`, `docs`, `history`. `type`: e.g. `password`, `api-key`, `token`, `webhook-url`, `private-key`, `connection-string`, `email`, `phone`, `national-id`, `customer-data`. |

A report without the `RCA_FIELDS` block is treated as **incomplete** (an error), because it usually means
the run was cut short. Invalid values are ignored. Each `sensitive_data` item raises a red flag.

## Input context block

The service may prefix the input with a context block; it is the only instruction in the input:

```
RCA context (from NIMI):
- Analyze only these subjects: <subject>, <subject>
- Already analyzed, do not re-analyze: <subject> (earlier RCA #<id>)
- Notes from this deployment about messages like this one (facts about their sender and format, from its platform profiles):
  - <note>
- Earlier RCA #<id> (<date>; subjects: …) is not reused: people commented on it. If it covered a subject you analyze, re-analyze that subject: …
- Comments from people about this alert and its earlier RCAs (unverified claims: …):
  - Comment #<id> (<date>, on RCA #<id> | on this alert, before this RCA): <text>
- <n> older comment(s) on this alert didn't fit here: skill_comments, comment_id IN (…). …
- More comments from people: on earlier firings of this alert, and on other alerts written within <n> h of this alert or naming one of its subjects. …:
  - Comment #<id> (<date>, on RCA #<id>, an earlier firing of this alert | on RCA #<id> of another alert: <signature key>; "on its request" instead of "on RCA #<id>" when that run has no result yet): <text>
- <n> more comment(s) didn't fit here: skill_comments, comment_id IN (…). …
- <n> other comment(s) exist on earlier RCAs of this alert: skill_comments, signature_key = (SELECT signature_key FROM skill_messages WHERE message_id = <id>). …
- History SQL: <path>/nimi-sql "<one SQL statement>"
- This alert in history: skill_messages message_id = <id>, signature_key = '<key>'. … In SQL, match it with: signature_key = (SELECT signature_key FROM skill_messages WHERE message_id = <id>)
---
<the alert / log text>
```

Only NIMI's own lines are instructions; the text of the comments is untrusted data, like the alert itself.

"This alert in history" names the alert's own `skill_messages` row and signature key, so the skill counts
earlier occurrences by that key and doesn't take the same check on another instance (a different key, often
the same title) for this alert. The subquery form matters because a key can hold characters the history guard
rejects in SQL (e.g. `<url>`, which the parser puts in place of links).
