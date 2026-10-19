# Security model

NIMI feeds untrusted text (alerts, logs, anything a system or person posts in a monitored channel) to
language models that can read code. The design assumes that text may try to manipulate the model, and
limits what any model can do regardless of what it is told.

## Boundaries

| Actor | Can | Cannot |
|---|---|---|
| **RCA skill** | Read registered repos and its own folder; read-only `git`; plain-text web search; read-only SQL over two views. | Write or modify anything; run other commands or scripts; open URLs (including links in alerts); read the service code, config or credentials; use MCP servers. |
| **Triage model** | Read the prompt the service builds. | Use any tool. |
| **Service** | Read/write its database; read/post in registered channels as the configured user; call the admin webhook. | (Code, not a model: it never executes model output.) |

The skill's limits are **enforced** by the guard hook (allowlist, fails closed) and by Postgres
permissions, not only by instructions. The service code, its settings and the reader's credentials live
outside every path the guard allows.

## Untrusted input

- The skill and triage prompts mark message text as untrusted data. Only the service's context block at
  the very start of the skill's input is an instruction.
- URLs in input are never opened: `WebFetch` is denied, and web searches may not contain URLs, domains or
  IPs, or any internal data.
- Model output is only stored and posted as text; replies are masked for secret formats before posting.

## Sensitive data

There is no free-text redaction. Instead NIMI **detects, records and alerts**:

- a code scanner runs before any model; critical secrets **hold** the request from all models;
- triage and the skill report types (never values) of sensitive data they notice;
- red flags record type and location only and notify the admin webhook;
- the skill is instructed never to write sensitive values; outgoing replies are masked as a backstop.

See [Red flags](../guides/red-flags.md).

## Least-privilege database access

Three roles: the service (owner), the skill reader (two views, read-only, 5s timeout), and an optional
development review role (one view). Even unrestricted SQL from the skill can't read other tables, write,
create objects, read server files or run longer than 5 seconds. See [Data model](data-model.md#database-roles).

## Claude environment

Claude calls get a clean environment: unrelated API keys, base URLs or model overrides from the shell are
not passed through; the account comes only from NIMI's settings. Triage loads no settings layers at all;
RCA runs load `user,project` by default and no MCP servers. Your organization's managed policy always
applies.

## Local machine

NIMI writes only to its database, `~/.cache/nimi/` (triage working directory, per-run temporary files),
project copies you opted into `sync` for (fetch + checkout, never when they have uncommitted changes or
local commits),
`~/.local/state/nimi/guard-denials.jsonl`, and the Google Chat token when it refreshes
(`~/.config/nimi/gchat-token.json`, chmod 600). Registered repos are only read; the skill can't read the
token or any settings file.
