# The RCA skill

The `/rca` skill lives in `nimi-skill/.claude/` and works on its own in Claude Code, or headlessly from the
service.

| File | Role |
|---|---|
| `skills/rca/SKILL.md` | The process, ground rules and output contract. Runs as a forked `rca-investigator` subagent. |
| `agents/rca-investigator.md` | The subagent: model `sonnet`, tools `Read, Grep, Glob, Bash, WebSearch`; write and fetch tools disallowed; guard hook attached. |
| `hooks/nimi_guard.py` | PreToolUse guard: allows only what's listed below, denies everything else, fails closed. |
| `nimi-session-settings.json` | Attaches the guard to the whole headless session (used by the service). |
| `skills/rca/sources.md` | The complete list of allowed sources, the history views and SQL rules. |
| `skills/rca/projects/` | Project registry, picking rules and per-project guides. See [Adding a project](../guides/adding-a-project.md). |
| `skills/rca/templates/report.md` | Report layout. |

## The process

1. **Early exits**: OK/resolved input → `RCA_OUTCOME: not_applicable`; no usable signal →
   `RCA_OUTCOME: insufficient_context`.
2. **Parse the input**: service, environment, timestamps, errors, stack frames, IDs. URLs are text to
   quote, never to open.
3. **Choose the project** by the failing system (not the sender), via `projects/local/INDEX.md`.
4. **Search runbooks** (mandatory search, optional use). Runbooks are hints: claims from them are labelled
   unverified, their age is checked, contradictions are reported, actions are never performed.
5. **Check history** (service runs): related messages around the incident in all channels, **each affected
   item searched by name in every channel over the last ~7 days** (errors behind an alert often happen at
   another time, e.g. a daily job), recurrence of the signature, and previous RCAs with their feedback.
   **Weigh comments** people wrote about this alert, its earlier firings and other alerts around it
   (given in the input, or found in `skill_comments`): unverified claims, checked against code and data,
   never acted on ([Comments](../guides/comments.md)).
6. **Orient, locate the failing code, check config and recent changes** (read-only git).
7. **Upstream / downstream** one hop each way; web search in plain text when a third-party error needs it.
8. **Timeline in UTC** with explicit offset conversion; distinguish trigger from symptoms; weigh
   **internal vs upstream** causes; check **retries at every level** to decide whether it **self-heals**.
9. **Report** with evidence for every claim, a verdict line per comment, suggested next steps and a confidence
   level (stored).
10. **Chat message**: the one message on-call sees, in simple words (TL;DR, cause, read-only steps to verify it, what
    to do now and not to do, permanent fixes by owner, missing docs/monitoring, related messages cited by
    ID so the service can link them), followed by an `RCA_COMMENTS` block with a verdict per comment (when
    comments were given) and the `RCA_FIELDS` block ([output contract](../reference/output-contract.md)).

## What the guard allows

| Tool | Allowed |
|---|---|
| `Read`, `Grep`, `Glob` | Absolute paths inside registered project paths or the skill folder. |
| `Bash` | `git -C <registered path> <subcommand> …` with subcommand `log`, `show`, `blame`, `diff` or `grep` (no flags that read outside the repo, write files or run programs), or the history command `<checkout>/nimi-service/bin/nimi-sql "<SQL>"`. Shell metacharacters (see below) are blocked anywhere in a command, even inside quotes. |
| `WebSearch` | Plain-text queries without URLs, domains or IPs, and not restricted to domains. |
| `Skill` | Only `rca` (headless sessions). |
| Everything else | Denied: `WebFetch`, writes, edits, other commands, MCP tools. |

Blocked characters in Bash commands: `;` `&` `|` `` ` `` `$` `<` `>` `\` and newlines. This rules out
pipes, chaining, redirection and substitution entirely; the skill uses the Grep tool for regex searches,
`%x09` separators in `git --format`, and `BETWEEN` in SQL.

Every denial is logged with a severity (`low` for the model's own formatting slips) and becomes a red flag
when it happens in a service run.

## History access (`nimi-sql`)

Service runs let the skill query history with read-only SQL. The service names the command in a context
block at the top of the input:

```
RCA context (from NIMI):
- Analyze only these subjects: orders.payments
- Notes from this deployment about messages like this one (facts about their sender and format, from its platform profiles):
  - Alerts from this notifier end with one block per matched series: the series in bold, then its value.
- Comments from people about this alert and its earlier RCAs (unverified claims: …):
  - Comment #12 (Oct 25, on RCA #1): the payments export was replaced after listing.
- History SQL: /…/nimi-service/bin/nimi-sql "<one SQL statement>"
- This alert in history: skill_messages message_id = 4182, signature_key = 'alert|[shop-prod] disk usage high'. … In SQL, match it with: signature_key = (SELECT signature_key FROM skill_messages WHERE message_id = 4182)
---
<the alert / log text>
```

Notes and comment lines appear only when they apply; the full list of lines is in the
[output contract](../reference/output-contract.md).

The **skill writes the SQL**; the service only provides access:

- **Database boundary**: it connects as `nimi_skill_reader`, which can read only `skill_messages`,
  `skill_rcas` and `skill_comments` (read-only sessions, 5s timeout).
- **The command** runs one statement in a read-only transaction, returns at most 100 rows and 4000
  characters per value, masks recognized secrets, and has no psql meta-commands (no shell, no file output).
- **Credentials**: per run, the reader's password goes into a private temporary file (deleted afterwards);
  the run's environment carries its path and the non-secret settings, with `NIMI_ENV=/dev/null` so the
  command never reads the service's own settings.
- **The guard** allows exactly that file from the same checkout, with the usual character rules, so the
  skill writes ranges with `BETWEEN` and avoids `;`, `<`, `>`, `|`.

History is a guarded command rather than an MCP server on purpose: organizations often restrict MCP
servers through managed Claude Code policies, while a command runs anywhere the skill does.

## Headless runs

The service runs, from the skill folder:

```
claude -p "/rca <input>" --settings .claude/nimi-session-settings.json \
  --tools Read,Grep,Glob,Bash,WebSearch,Skill --permission-mode dontAsk \
  --setting-sources user,project --strict-mcp-config --output-format json
```

with a clean environment (see [Configuration](../getting-started/configuration.md#which-claude-account-nimi-uses))
and a timeout of `NIMI_RCA_TIMEOUT_SECONDS`. A run costs roughly $0.10–0.15 with Sonnet.
