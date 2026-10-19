# Installation

This page sets everything up once: the projects the skill analyzes, the Python environment, Google Chat
access, Claude, and the database. Then continue with [First run](first-run.md).

## Prerequisites

| What | Notes |
|---|---|
| Linux or macOS, `git` | |
| Python 3.10+ | 3.11+ recommended (Google's client libraries are dropping 3.10). |
| PostgreSQL 13+ | Tested on 18. Local or managed. The `pg_trgm` extension must be available (it ships with Postgres). |
| Claude Code CLI | Logged in (`claude`, then `/login`), or an API key — see [Configuration](configuration.md#which-claude-account-nimi-uses). |
| Local checkouts of the repos to analyze | E.g. your service repos. Read-only use: NIMI never modifies them. |
| A Google Cloud OAuth client for the Chat API | Desktop-app OAuth client (`client_secret.json`) with the Chat API enabled. Only needed for the service, not for running `/rca` by hand. |

## 1. Get the code

```bash
git clone <nimi repo> ~/nimi        # any location works; nothing depends on the path
cd ~/nimi
```

## 2. Register the projects the skill may read

The skill may only read code inside registered project paths (enforced by its guard hook).

```bash
cd nimi-skill/.claude/skills/rca/projects
cp -r example local
# edit local/registry.json (absolute checkout paths), local/INDEX.md and one local/<name>.md per project
```

Everything in `projects/local/` describes *your* systems and is git-ignored; `projects/example/` is the
template. See [Adding a project](../guides/adding-a-project.md).

At this point the skill already works by hand: `cd nimi-skill && claude`, then `/rca <alert text>` — see
[Running an RCA by hand](../guides/manual-rca.md). The rest of this page is for the service.

## 3. Python environment

```bash
cd nimi-service
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q tests          # optional: should pass
```

## 4. Google Chat access

NIMI reads (and, when enabled, posts) as **your user** with an OAuth token. Follow
[Google Chat access](google-chat-access.md): enable the Chat API in a Google Cloud project, configure
the Chat app and the consent screen, create a **Desktop app** OAuth client, then log in once:

```bash
.venv/bin/python -m nimi_service gchat-login --client-secret ~/Downloads/client_secret_XXXX.json
.venv/bin/python -m nimi_service gchat-spaces          # note the IDs of your alert and log spaces
```

## 5. Database

### Local Postgres: `db/bootstrap.sh`

```bash
bash nimi-service/db/bootstrap.sh          # uses sudo -u postgres for CREATE ROLE / DATABASE
```

It creates:

- the **service role** (`nimi_service`), owner of the **database** (`nimi`) and the only role that can
  create objects;
- the **skill reader role** (`nimi_skill_reader`): read-only, used by the skill's history queries;
- `~/.config/nimi/service.env` (chmod 600) with the connection settings and generated passwords (never
  printed).

To write the settings into the repo folder instead (`nimi-service/.env`, git-ignored, read by the service
and overriding `~/.config/nimi/service.env`):

```bash
NIMI_ENV_FILE_OUT=~/nimi/nimi-service/.env bash nimi-service/db/bootstrap.sh
```

Names are configurable: `NIMI_DB_NAME=rca NIMI_DB_USER=rca_svc NIMI_DB_SKILL_USER=rca_skill bash …`.
The script is safe to re-run with the same `NIMI_ENV_FILE_OUT` (it reads names and passwords back from that
file and re-syncs the role passwords). Passwords are handed to `psql` on standard input, never as
command-line arguments.

### Managed Postgres / infrastructure as code

Skip the script. Create:

1. a database and a login role that owns it (the service role);
2. a second login role for the skill reader with `default_transaction_read_only = on`,
   `statement_timeout = '5s'`, `CONNECT` on the database and nothing else;

then set the `NIMI_DB_*` settings ([Configuration](configuration.md#database)). Passwords can come from a
secrets manager (`NIMI_DB_PASSWORD_FILE`, `NIMI_DB_SKILL_PASSWORD_FILE`) or `~/.pgpass`.

### Create the schema

```bash
cd nimi-service
.venv/bin/python -m nimi_service db upgrade
```

This creates all tables and views and grants the skill reader access to its two views. Re-run it after
pulling changes that include migrations.

## 6. Check the configuration

```bash
.venv/bin/python -m nimi_service config      # every setting, its value (secrets masked) and source
.venv/bin/python -m nimi_service db current  # schema version: should say "up to date"
```

Next: [First run](first-run.md).
