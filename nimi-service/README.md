# nimi-service

Polls registered chat channels, stores every message in Postgres, triages new bot alerts, runs the `/rca`
skill (in `../nimi-skill`) for the ones that need it, and posts the result in the alert's thread.

Documentation: [`../docs`](../docs/index.md) — especially
[Installation](../docs/getting-started/installation.md), [Operating](../docs/guides/operating.md),
[Architecture](../docs/architecture/overview.md) and the [CLI](../docs/reference/cli.md) /
[settings](../docs/reference/settings.md) reference.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
bash db/bootstrap.sh                                  # local Postgres: database + roles (sudo -u postgres)
.venv/bin/python -m nimi_service db upgrade           # tables, views, skill reader grants
.venv/bin/python -m nimi_service add-channel gchat <space id> alerts --rca
.venv/bin/python -m nimi_service ingest-once          # first poll backfills; no RCAs for backfill
.venv/bin/python -m nimi_service run
```

Register the repos the skill may read first (`nimi-skill/.claude/skills/rca/projects/local/`; see
[Installation](../docs/getting-started/installation.md#2-register-the-projects-the-skill-may-read)).

## Tests

```bash
.venv/bin/python -m pytest -q tests
```
