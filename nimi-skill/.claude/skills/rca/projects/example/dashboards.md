# Project: dashboards

<!-- Example project guide for a second repository (here: a BI / dashboarding tool). -->

BI / dashboarding tool (example): queries, dashboards, query-result alerts, background workers.

## Repo
`<dashboards>` below stands for this project's path in `projects/local/registry.json`; `<shop>` likewise for
the shop project's.

- **Path:** `<dashboards>`
- **Git:** `git -C <dashboards> log|show|blame|diff|grep ...`

## Orient first
- `<dashboards>/README.md`

## Where things live
- **Query execution:** `<dashboards>/src/queries/`
- **Alerts:** `<dashboards>/src/alerts/`
- **Workers and scheduler:** `<dashboards>/src/workers/`

## Runbooks
- Kept in the shop repository: `<shop>/docs/runbooks/dashboards.md`

## Project-specific notes
- An alert *sent by* the dashboards tool is usually about the data it queries: pick the project that owns
  that data (see INDEX.md).
