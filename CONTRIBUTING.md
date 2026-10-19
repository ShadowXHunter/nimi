# Contributing to NIMI

Thanks for helping! Issues and pull requests are welcome. For security problems, see
[SECURITY.md](SECURITY.md) instead of opening an issue.

## Development setup

```bash
cd nimi-service
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q tests
```

Unit tests need no database, network or Claude account. For anything touching the schema, use a
throwaway Postgres (see [docs/development/setup.md](docs/development/setup.md)).

Docs:

```bash
python3 -m venv .venv-docs && .venv-docs/bin/pip install -r docs/requirements.txt
.venv-docs/bin/mkdocs build --strict     # or `mkdocs serve` to preview
```

CI runs both on every pull request.

## Guidelines

- **No real data, anywhere.** Code, tests, docs and examples must not contain real company names, chat
  space or user IDs, hostnames, log lines, table names or incidents. Use the fictional "shop" platform the
  tests and examples use (`com.example.shop`, `order-worker`, `catalog-api`, `AAAAxxxxxxx`).
- **Deployment content stays local.** Your projects (`nimi-skill/.claude/skills/rca/projects/local/`) and
  platform profiles (`nimi-service/parsers/local/`) are git-ignored; contribute generic improvements to the
  code or the `example`/`examples` templates instead.
- **Security boundaries need tests.** Changes to the guard hook, the database roles/views, `nimi-sql` or
  secret handling must come with tests for what is allowed **and** for near-misses that must stay denied.
  The guard is an allowlist that fails closed; keep it that way.
- **Settings and commands are documented.** A new setting goes into `config.py` (`Settings` and
  `ENV_VARS`), `env.example` and `docs/reference/settings.md`; a new CLI command into `__main__.py`'s
  docstring and `docs/reference/cli.md`. Tests fail if the reference pages miss one.
- **Schema changes are migrations.** Edit `models.py`, generate one with `db revision`, review it, and test
  upgrade and downgrade on a scratch database. Never edit a migration that has been released.
- **Parser changes are versioned.** Bump `RULES_VERSION` in `parse.py` when its output changes.
- Match the surrounding style: small functions, comments only where they explain *why*.

## Pull requests

- Keep them focused; describe the problem, the change and how you tested it.
- Make sure `pytest` and `mkdocs build --strict` pass.

## License

By contributing, you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE), the same license as the project.
