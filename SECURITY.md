# Security policy

NIMI feeds untrusted text (alerts, logs) to language models that can read code, so its safety boundaries
matter as much as its features. The design is described in
[docs/architecture/security.md](docs/architecture/security.md).

## Reporting a vulnerability

**Please don't open a public issue for security problems.** Report them privately through GitHub's
private vulnerability reporting: the **Security** tab of this repository → **Report a vulnerability**.

Include what you found, how to reproduce it, and the impact you expect. We aim to acknowledge reports
within a few days and to agree on a disclosure timeline with you.

## What we consider security issues

- **Guard hook bypasses:** the RCA skill reading files outside the registered project paths or its own
  folder, running commands other than the allowed read-only `git` and `nimi-sql` forms, opening URLs, or
  modifying anything locally.
- **Database privilege escapes:** the skill reader role (or the history command) reading tables other than
  `skill_messages` / `skill_rcas`, writing, or bypassing its read-only session or timeout.
- **Prompt injection with effect:** message text that makes triage or the skill do something the code
  limits are meant to prevent (e.g. reuse a candidate that wasn't offered, act outside the guard).
- **Secret exposure:** credentials, tokens or sensitive values from messages, settings or the database
  ending up in replies, logs, red flags, admin notifications, or anywhere the skill can read.
- **Credential handling:** the service's settings, the chat token or the skill reader's password becoming
  readable by the skill or other local users.

Out of scope: problems that require an attacker who can already modify NIMI's code, settings or local
files, and the behaviour of third-party services (the chat provider, Claude, Postgres) themselves.

## Supported versions

Security fixes go to the `main` branch. Please reproduce on the latest `main` before reporting.

## Hardening checklist for operators

- Keep `nimi-service/.env` / `~/.config/nimi/service.env` and the chat token chmod 600 and out of git.
- Use separate Postgres roles as created by `db/bootstrap.sh`; never give the skill reader more grants.
- Register only the repositories the skill needs (`projects/local/registry.json`).
- Set `NIMI_ADMIN_WEBHOOK` so red flags (secrets in messages, guard denials, errors) reach a human.
