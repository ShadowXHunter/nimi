# NIMI 👁️

**Notification & Incident Mitigation Intelligence**

NIMI reads alerts and error logs as they arrive in chat, works out the likely root cause, and replies in
the alert's thread with what happened and what to do about it — so by the time on-call opens their eyes
to an alert, the RCA is already there.

*In Hindu mythology, King Nimi was granted a place in the eyelids of all living beings; the blink —
nimisha — is named after him. NIMI lives in that moment between an alert arriving and someone looking at it.*

## What it does

1. **Ingests** every message from registered chat channels (Google Chat today) into Postgres.
2. **Skips** what needs no RCA: OK/resolved, informational and test messages, and your exclusion rules.
3. **Triages** the rest: repeats of an ongoing incident reuse the earlier RCA.
4. **Investigates** new incidents with the `/rca` Claude Code skill — code, config, git history, runbooks
   and its own alert history — read-only, enforced by a guard hook.
5. **Replies** in the thread with root cause, evidence, confidence and suggested next steps (suggestions
   only — NIMI never acts on systems), and **learns** from 👍 / 🤔 / 👎 reactions.

## Documentation

Full docs are in [`docs/`](docs/index.md) — plain Markdown, readable here — and render as a website:

```bash
python3 -m venv .venv-docs && .venv-docs/bin/pip install -r docs/requirements.txt
.venv-docs/bin/mkdocs serve          # http://127.0.0.1:8000
```

| Start here | |
|---|---|
| [Installation](docs/getting-started/installation.md) · [First run](docs/getting-started/first-run.md) | Set it up |
| [Architecture overview](docs/architecture/overview.md) · [Security model](docs/architecture/security.md) | How it works and what it may access |
| [Operating the service](docs/guides/operating.md) | Day-to-day use |
| [Developer setup](docs/development/setup.md) | Changing the code |
| [CLI](docs/reference/cli.md) · [Settings](docs/reference/settings.md) | Reference |

## Layout

| Path | What it is |
|---|---|
| [`nimi-skill/`](nimi-skill/) | The `/rca` skill, its read-only subagent and the guard hook. |
| [`nimi-service/`](nimi-service/) | The service: ingestion, triage, RCA runs, replies, feedback, red flags. |
| [`docs/`](docs/) | Documentation (MkDocs). |

## Status

The skill works and has been tested on real alerts with confirmed root causes. The service is built and
tested end to end; posting to chat is off by default (dry run) until you enable it. See the
[roadmap](docs/roadmap.md).

## Contributing, security and license

- [CONTRIBUTING.md](CONTRIBUTING.md): development setup, tests, guidelines (no real data anywhere).
- [SECURITY.md](SECURITY.md): how to report vulnerabilities privately; what counts as one.
- Licensed under the [Apache License 2.0](LICENSE). See [NOTICE](NOTICE).
