# Configuration

All settings are environment-style `NIMI_*` variables. The complete list with defaults is in the
[settings reference](../reference/settings.md); `nimi-service/env.example` is a commented template.

## Where settings come from

Later sources override earlier ones:

1. built-in defaults
2. `~/.config/nimi/service.env` — outside the repo (created by `db/bootstrap.sh`)
3. `nimi-service/.env` — inside the repo folder, git-ignored (easy to find)
4. real environment variables

`NIMI_ENV=/path/to/file` replaces 2 and 3 with exactly that file. To check what is in effect and where
each value came from (secrets masked):

```bash
.venv/bin/python -m nimi_service config
```

## Database

Connection details are separate settings — not a URL — so credentials can be injected from a secrets
manager (Vault Agent, Kubernetes secrets, …):

| Setting | Default |
|---|---|
| `NIMI_DB_HOST` / `NIMI_DB_PORT` / `NIMI_DB_NAME` | `127.0.0.1` / `5432` / `nimi` |
| `NIMI_DB_USER` | `nimi_service` |
| `NIMI_DB_PASSWORD` or `NIMI_DB_PASSWORD_FILE` | unset: libpq falls back to `~/.pgpass` / trust / peer auth |
| `NIMI_DB_SSLMODE` | unset (e.g. `require` for managed Postgres) |
| `NIMI_DB_SKILL_USER` | `nimi_skill_reader` |
| `NIMI_DB_SKILL_PASSWORD` or `NIMI_DB_SKILL_PASSWORD_FILE` | unset |

## Which Claude account NIMI uses

Triage and RCA runs start `claude` with a **clean environment**: variables such as `ANTHROPIC_API_KEY` or
`ANTHROPIC_BASE_URL` exported in your shell for other work are *not* passed through. Only these settings
decide:

| Setting | Effect |
|---|---|
| `NIMI_CLAUDE_CONFIG_DIR` | A separate Claude login and user config, e.g. `~/.claude-nimi` (log in once with `CLAUDE_CONFIG_DIR=~/.claude-nimi claude`). Unset = your default `~/.claude`. Keep it outside the repo: it holds login tokens. |
| `NIMI_ANTHROPIC_API_KEY_FILE` | Use an API key read from this file (API billing) instead of the stored login. |
| `NIMI_CLAUDE_SETTING_SOURCES` | Settings layers RCA runs load: default `user,project`. `project` ignores your user-level settings, skills and plugins. Triage never loads any. |
| `NIMI_CLAUDE_PASS_ENV` | Extra variables to pass through, e.g. `HTTPS_PROXY,NO_PROXY`. |
| `NIMI_CLAUDE_BIN` | The `claude` executable (default: `claude` on `PATH`). |

With nothing set, everything uses your normal login and `~/.claude` config. Your organization's managed
Claude Code policy always applies on top.

## Settings you are most likely to change

| Setting | Default | Meaning |
|---|---|---|
| `NIMI_POST_ENABLED` | `false` | Post replies to chat. Off = dry run (results are stored, not posted). |
| `NIMI_POST_TARGET` / `NIMI_POST_WEBHOOK` | `thread` / unset | Reply in the alert's thread, in a separate space via an incoming webhook, or both ([Where replies go](../guides/where-replies-go.md)). |
| `NIMI_ADMIN_WEBHOOK` | unset | Incoming webhook of the admin's chat space for [red flags](../guides/red-flags.md). |
| `NIMI_ALERT_SENDERS` | any bot | Comma-separated sender IDs allowed to trigger RCAs. |
| `NIMI_STALE_HOURS` | `6` | Alerts older than this when NIMI can process them get no RCA. |
| `NIMI_MAX_PER_DAY` / `NIMI_MAX_PARALLEL` | `50` / `2` | Daily cap on RCA runs (UTC) / concurrent runs. |
| `NIMI_TRIAGE_MODEL` | `haiku` | Model for triage. |
| `NIMI_SKILL_HISTORY` | `true` | Let RCA runs query past messages and RCAs. |
| `NIMI_REPLY_DISCLAIMER` | built-in | The disclaimer on the first line of every RCA message (`👁️ NIMI — <disclaimer>`), e.g. to say where NIMI runs. |

## Secrets

Secret values (`NIMI_DB_PASSWORD`, `NIMI_DB_SKILL_PASSWORD`, `NIMI_ADMIN_WEBHOOK`, `NIMI_POST_WEBHOOK`) are masked by `config`
and never logged. Prefer the `*_FILE` variants with a secrets manager. Never put real values in
`env.example`: it is committed.
