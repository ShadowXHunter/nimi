# Project: shop

<!-- Example project guide. Replace with a description of your own repository. Keep it short and concrete:
the skill reads it before searching, so pointers to the right directories save a lot of time. -->

Online shop backend (example): REST API, order and payment workers, scheduled jobs. **Default project.**

## Repo
`<shop>` below stands for this project's path in `projects/local/registry.json` (an absolute path).

- **Path:** `<shop>` (a NIMI-only copy of the shop repository; refer to files as `shop: <path in repo>`)
- **Git:** `git -C <shop> log|show|blame|diff|grep ...`
- **Checkout ≠ production:** the local checkout may differ from what is deployed. Record the commit you
  analyzed: `git -C <shop> log -1 --date=iso --format="%h %ad %s"`

## Orient first
- `<shop>/README.md` — services and how they fit together
- `<shop>/docs/` — component docs

## Where things live
- **Service code:** `services/<service>/` (e.g. `services/api/`, `services/order-worker/`)
- **Config:** `config/<env>/<service>.yaml`; deployment specs in `deploy/<service>/`
- **Dependencies:** `pyproject.toml` / `package.json` per service
- **Database migrations:** `migrations/`

## Runbooks (search is mandatory — see SKILL.md step 3)
- `<shop>/docs/runbooks/` — on-call runbooks, one section per alert
- Runbook age: `git -C <shop> log -1 --date=iso --format="%h %ad" -- <runbook path>`

## Project-specific notes
- **Retries:** failed jobs are retried by the job runner (`services/jobs/runner.py`); check it before
  calling a failure "not self-healing".
- **Upstream dependencies:** payment provider webhooks and the warehouse API; their outages show up as
  5xx / timeouts in `order-worker` logs.
