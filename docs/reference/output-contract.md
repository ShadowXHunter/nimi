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
`[label](rca:<result_id>)`; for code, `[label](code:<project key>@<commit>/<path in repo>#L<line>)`.
Before posting, the service checks each one: cited **bot** messages become links
to the original message, cited RCAs link to their post (in the same destination when there is one), and
cited code links to the file at that commit on the project's code host, and anything else — unknown IDs
or projects, human messages, unsafe paths, Markdown links to URLs — is reduced to its label. Absolute local
paths of registered checkouts are rewritten to `<project>: <path in repo>`. The stored
report keeps the citations, so it records which messages each RCA relied on.

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
| `origin` | `internal` (our code, config, infra, deploy), `upstream` (source system, third-party API/SDK, data), `undetermined`. |
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
- History SQL: <path>/nimi-sql "<one SQL statement>"
---
<the alert / log text>
```
