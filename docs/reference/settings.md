# Settings

Every setting, its default and meaning. See [Configuration](../getting-started/configuration.md) for
where settings are read from. Secret settings are masked by `python -m nimi_service config`.
`<repo>` is the NIMI checkout.

## Database

| Setting | Default | Meaning |
|---|---|---|
| `NIMI_DB_HOST` | `127.0.0.1` | Postgres host. |
| `NIMI_DB_PORT` | `5432` | Postgres port. |
| `NIMI_DB_NAME` | `nimi` | Database name. |
| `NIMI_DB_USER` | `nimi_service` | The service role. |
| `NIMI_DB_PASSWORD` | unset | Service role password (secret). Takes priority over the file. |
| `NIMI_DB_PASSWORD_FILE` | unset | File containing only the service password (e.g. a mounted secret). |
| `NIMI_DB_SSLMODE` | unset | libpq `sslmode`, e.g. `require`. |
| `NIMI_DB_SKILL_USER` | `nimi_skill_reader` | The skill's read-only role (history SQL). |
| `NIMI_DB_SKILL_PASSWORD` | unset | Skill reader password (secret). |
| `NIMI_DB_SKILL_PASSWORD_FILE` | unset | File containing only the skill reader password. |

Without a password or file, libpq falls back to `~/.pgpass`, trust or peer authentication.
`NIMI_DB_DSN` is no longer supported.

## Chat providers

| Setting | Default | Meaning |
|---|---|---|
| `NIMI_GCHAT_TOKEN_FILE` | `~/.config/nimi/gchat-token.json` | Google Chat user OAuth token (created by `gchat-login`). |

## Polling and history

| Setting | Default | Meaning |
|---|---|---|
| `NIMI_POLL_SECONDS` | `60` | Pause between poll cycles. |
| `NIMI_BACKFILL_DAYS` | `90` | Default history pulled on a channel's first poll (`add-channel --backfill-days` overrides). |
| `NIMI_REACTION_DAYS` | `7` | Reactions are re-checked on replies posted within this many days. |

## Triage and RCA

| Setting | Default | Meaning |
|---|---|---|
| `NIMI_TRIAGE_MODEL` | `haiku` | Model for triage. |
| `NIMI_REUSE_DAYS` | `7` | Earlier RCAs within this window can be reused. |
| `NIMI_ALERT_SENDERS` | any bot | Comma-separated sender IDs (e.g. `users/123`) allowed to trigger RCAs. |
| `NIMI_MAX_PER_DAY` | `50` | Maximum RCA runs per UTC day; further requests are skipped (triage is not capped). |
| `NIMI_MAX_PARALLEL` | `2` | Concurrent RCA runs. |
| `NIMI_RCA_TIMEOUT_SECONDS` | `900` | Timeout of one skill run. |
| `NIMI_STALE_HOURS` | `6` | Alerts older than this when NIMI can process them get no RCA. |
| `NIMI_SKILL_HISTORY` | `true` | Give RCA runs read-only history SQL (`nimi-sql`). |
| `NIMI_RCA_PROJECT` | `<repo>/nimi-skill` | Where the `/rca` skill lives. |
| `NIMI_SYNC_MINUTES` | `10` | How often projects with `sync` in the registry are updated (only between RCA runs). |

## Claude

| Setting | Default | Meaning |
|---|---|---|
| `NIMI_CLAUDE_BIN` | `claude` | The Claude Code executable. |
| `NIMI_CLAUDE_CONFIG_DIR` | unset (`~/.claude`) | Separate Claude login and user config for NIMI. |
| `NIMI_ANTHROPIC_API_KEY_FILE` | unset | Use an API key from this file instead of the stored login. |
| `NIMI_CLAUDE_SETTING_SOURCES` | `user,project` | Settings layers RCA runs load (subset of `user,project,local`; empty = none). |
| `NIMI_CLAUDE_PASS_ENV` | unset | Extra environment variables passed to Claude, e.g. `HTTPS_PROXY,NO_PROXY`. |
| `NIMI_LIMIT_COOLDOWN_MINUTES` | `15` | Pause length after a usage limit when Claude gives no reset time. |

## Replies and notifications

| Setting | Default | Meaning |
|---|---|---|
| `NIMI_POST_ENABLED` | `false` | Post replies to chat (off = dry run). |
| `NIMI_POST_TARGET` | `thread` | Where replies go: `thread` (in the alert's thread), `webhook` (a separate space, with a link back to the alert) or `both`. See [Where replies go](../guides/where-replies-go.md). |
| `NIMI_POST_WEBHOOK` | unset | Incoming webhook URL of the destination space for `webhook` / `both` (secret). |
| `NIMI_POST_MAX_CHARS` | `4000` | Maximum reply length (the report body is truncated; notes and the feedback line are kept). |
| `NIMI_REPLY_DISCLAIMER` | built-in | Disclaimer on the first line of every RCA message (default: "Automated RCA, not verified. Based on code and docs that may differ from production."). |
| `NIMI_ADMIN_WEBHOOK` | unset | Incoming webhook of the admin's chat space for red flags (secret). |
| `NIMI_GUARD_DENIAL_LOG` | `~/.local/state/nimi/guard-denials.jsonl` | Where the guard hook logs denials. |

## Special variables

| Variable | Meaning |
|---|---|
| `NIMI_ENV` | Read exactly this settings file instead of `~/.config/nimi/service.env` and `nimi-service/.env`. |
| `NIMI_PYTHON` | Set by the service for `bin/nimi-sql`: the Python to run it with. |
