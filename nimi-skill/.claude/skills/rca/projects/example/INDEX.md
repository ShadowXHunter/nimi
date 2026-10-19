# Projects

Pick the project(s) the input belongs to, then read **only** the matching `projects/local/<name>.md`
file(s). Allowed paths per project are in `projects/local/registry.json` (enforced by the guard). Read it:
project files write a project's path as `<name>` (e.g. `<shop>`), meaning that project's path from the
registry.

<!-- Example content: replace the rows with your projects. Signals are what identifies a project in an
alert or log: package / module names, service and deployment names, job names, alert titles. -->

| Project | What it is | Signals in an alert / log | File |
|---|---|---|---|
| **shop** (default) | Online shop backend: API, order workers, payment webhooks, scheduled jobs | `com.example.shop` packages; services like `shop-api`, `order-worker*`, `nightly-*` jobs; alerts about orders, payments, checkout latency | `projects/local/shop.md` |
| **dashboards** | BI / dashboarding tool: queries, dashboards, query alerts, workers | The tool's name; its query / dashboard / alert terms; its worker or scheduler names | `projects/local/dashboards.md` |

## Choosing

- **Pick by the system the alert is about**, not by who sent the message. A dashboard alert *about shop
  data* (e.g. "duplicate order IDs") is a **shop** problem: the dashboarding tool only delivered it.
- **No clear match → the default project.**
- **Seems unrelated to every project** (names a system or language none of them contain): do one short,
  targeted search of the default project for the service name, error text, or config keys. If that finds
  nothing, stop searching — give a clearly labelled hypothesis from the alert text (and web search), or
  report `insufficient_context` and list what is missing.
- **Spans projects** (e.g. a dashboard query failing because of a shop table): read both project files;
  analyze each part in its own project.

State in the report which project(s) you chose and why.
