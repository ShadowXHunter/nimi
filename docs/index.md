# NIMI 👁️

**Notification & Incident Mitigation Intelligence** — NIMI reads alerts and error logs as they arrive in
chat, works out the likely root cause, and replies in the alert's thread with what happened and what to do
about it. By the time on-call opens their eyes to an alert, the RCA is already there.

!!! info "The name"
    In Hindu mythology, King Nimi lost his body to a curse and was granted a place in the eyelids of all
    living beings; the blink — *nimisha* — is named after him. NIMI the service lives in that moment
    between an alert arriving and someone looking at it.

## What it does

1. **Ingests** every message from registered chat channels (Google Chat today) into Postgres.
2. **Skips** what needs no RCA: OK/resolved messages, informational and test messages, and anything matching
   your [exclusion rules](guides/skipping-messages.md).
3. **Triages** the rest: a repeat of an incident already analyzed reuses the earlier RCA instead of
   spending tokens again.
4. **Investigates** new incidents with the `/rca` Claude Code skill: reads the relevant project's code,
   config, git history and runbooks, looks up related alerts/logs and earlier RCAs in its history, weighs
   internal vs upstream causes, and checks whether the failure heals itself.
5. **Replies** in the thread with the root cause, evidence, confidence, and suggested next steps.
6. **Learns** from 👍 / 🤔 / 👎 reactions on its replies.

Mitigation steps are **suggestions only** — NIMI never acts on systems.

## Where to start

| You want to… | Read |
|---|---|
| Install and run NIMI | [Installation](getting-started/installation.md) → [First run](getting-started/first-run.md) |
| Understand how it works | [Architecture overview](architecture/overview.md) |
| Know what it can and cannot access | [Security model](architecture/security.md) |
| Operate it day to day | [Operating the service](guides/operating.md) |
| Change the code | [Developer setup](development/setup.md) |
| Look up a command or setting | [CLI](reference/cli.md) · [Settings](reference/settings.md) |

## Repository layout

| Path | What it is |
|---|---|
| `nimi-skill/` | The `/rca` Claude Code skill, its read-only helper agent (`rca-investigator`), and the guard hook that enforces what it may access. |
| `nimi-service/` | The service: ingestion, triage, running the skill, replies, feedback, red flags. Python, SQLAlchemy + Alembic, Postgres. |
| `docs/` | This documentation (Markdown; rendered with MkDocs). |

## Status

- **Skill:** working; tested on real alerts and logs with confirmed root causes.
- **Service:** built and tested end to end. Posting to chat is off by default (dry run): enable it with
  `NIMI_POST_ENABLED=true` once you are happy with the stored results.
- See the [roadmap](roadmap.md) for what's planned.

## License

NIMI is open source under the Apache License 2.0. Contribution guidelines and the security policy
(`CONTRIBUTING.md`, `SECURITY.md`) are at the root of the repository.
