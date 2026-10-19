# Developer setup

## Code layout

```
nimi/
├── nimi-service/
│   ├── nimi_service/          # the package (run as `python -m nimi_service`)
│   │   ├── __main__.py        # CLI
│   │   ├── config.py          # settings: defaults, env files, env vars
│   │   ├── models.py          # SQLAlchemy models (schema source of truth)
│   │   ├── views.py           # SQL views + skill grants
│   │   ├── db.py              # session helpers and queries
│   │   ├── ingest.py          # polling, storing, parsing, opening requests
│   │   ├── parse.py           # rule-based parser + signatures
│   │   ├── profiles.py        # platform profiles (parsers/local/*.yaml): layouts, masks, triage notes
│   │   ├── exclusions.py      # exclusion rules
│   │   ├── triage.py          # triage prompt, call, validation
│   │   ├── workflow.py        # request state machine, scheduling, posting
│   │   ├── rca.py             # headless skill runs, output parsing, reply formatting
│   │   ├── skill_sql.py       # nimi-sql (read-only history SQL)
│   │   ├── reactions.py       # reactions -> feedback
│   │   ├── scanner.py         # secret detection / masking
│   │   ├── redflags.py        # admin flags + webhook
│   │   ├── pause.py           # Claude pause state
│   │   ├── claude_cli.py      # clean env for claude, error classification
│   │   ├── guardlog.py        # reads the guard's denial log
│   │   └── providers/         # base interface + gchat/
│   ├── bin/nimi-sql           # wrapper the skill runs
│   ├── parsers/               # platform profiles: local/ (yours, git-ignored), examples/
│   ├── db/                    # bootstrap.sh, Alembic migrations
│   ├── dev/                   # development-only scripts
│   └── tests/
├── nimi-skill/.claude/        # the /rca skill, agent, guard hook; skills/rca/projects/{local,example}/
├── docs/                      # this documentation
└── mkdocs.yml
```

## Environment

```bash
cd nimi-service
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q tests
```

Unit tests need no database or network. They cover parsing, triage validation, the guard hook, output
parsing, formatting, settings, exclusions and history access.

## A scratch database

Use a throwaway Postgres container for anything touching the schema, so your real database is never
involved:

```bash
docker run -d --rm --name nimi-test-db -p 127.0.0.1:5441:5432 -e POSTGRES_PASSWORD=testpw postgres:18
PGPASSWORD=testpw psql -h 127.0.0.1 -p 5441 -U postgres -c "CREATE DATABASE nimi"

export NIMI_ENV=/dev/null NIMI_DB_PORT=5441 NIMI_DB_USER=postgres NIMI_DB_PASSWORD=testpw
.venv/bin/python -m nimi_service db upgrade
# … test …
docker rm -f nimi-test-db
```

`NIMI_ENV=/dev/null` makes sure none of your real settings files are read.

## Reviewing stored data

To let someone (or an agent) review stored messages without the service's read-write credentials:

```bash
bash dev/review_access.sh            # creates nimi_readonly: SELECT on review_messages and skill_rcas only; password in ~/.pgpass
bash dev/review_access.sh revoke     # removes it
```

Re-run it after migrations that recreate views (dropping a view drops its grants).

## Conventions

See also `CONTRIBUTING.md` at the repository root (pull requests, licensing, the no-real-data rule).
CI (`.github/workflows/ci.yml`) runs the tests on Python 3.10–3.12 and the strict docs build.


- Settings: add a field to `Settings` **and** an entry to `ENV_VARS` in `config.py`, a line to
  `env.example`, and a row to [the settings reference](../reference/settings.md) (a test checks this).
- CLI commands: add them to the module docstring of `__main__.py` and to
  [the CLI reference](../reference/cli.md) (a test checks this).
- Never log or store secret values; red flags carry types and locations only.
- Model calls go through `claude_cli.build_env` (clean environment).
- Keep the provider interface neutral: nothing outside `providers/` may know about a specific platform.
