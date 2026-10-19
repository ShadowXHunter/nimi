# Adding a project to analyze

A **project** is a code repository (or several) the skill may read for RCAs, e.g. a backend monorepo or a
dashboarding tool. Your projects live in `nimi-skill/.claude/skills/rca/projects/local/` (git-ignored: it
describes your systems). `projects/example/` is a committed template with two sample projects; start with
`cp -r example local`.

| File in `projects/local/` | Purpose |
|---|---|
| `registry.json` | Project keys, descriptions, the default, each project's **absolute** checkout path(s), and optionally `display_name`, `web_url` and `sync` (below). The guard hook reads it: only these paths (plus the skill folder) are readable, and `git -C` may only target them. Missing or invalid: nothing is readable (fail closed). |
| `INDEX.md` | How the skill picks a project from an alert: signals per project, the default, what to do when nothing matches. Without it the skill replies `insufficient_context`. |
| `<name>.md` | Per-project guide: repo layout, where to orient first, config and dependency files, runbook locations, project-specific notes. Paths are written as `<name>/…`, meaning the project's path from the registry. |

## Steps

1. Clone the repo into a NIMI-only folder (see below: not your working copy).
2. Add the project to `projects/local/registry.json` with its real absolute path:
   ```json
   "billing": {
     "description": "Billing service: invoices, payment webhooks, reconciliation jobs",
     "paths": ["/home/you/nimi-copies/billing"],
     "display_name": "acme/billing",
     "sync": "origin/main"
   }
   ```
3. Write `projects/local/billing.md` following `projects/example/shop.md`: path placeholder, how to orient,
   where code and config live, dependency files, runbook locations.
4. Add a row to `projects/local/INDEX.md` with the signals that identify it in alerts (package names, service names,
   alert senders).
5. Test with `/rca` on a real alert from that system.

## Use a dedicated copy, and keep it current

Point the registry at a checkout used **only by NIMI** (e.g. `~/nimi-copies/shop`), not your working copy:
local commits or uncommitted changes there would make RCAs describe code that isn't deployed. A copy goes
stale unless something updates it; each report records the commit it analyzed, but stale code still means
weaker RCAs. Two ways to keep it current:

- **Let NIMI sync it (opt-in):** add `"sync": "origin/main"` (`<remote>/<branch>`) to the project. Between RCA
  runs, at most every `NIMI_SYNC_MINUTES` (10), the service fetches and moves the copy to that ref. It
  **never touches a checkout with uncommitted changes or local commits**: it skips it and raises a red flag,
  so an accidentally registered working copy can't lose anything. The RCA skill itself stays read-only.
- **Do it yourself**, e.g. a cron job, if you prefer NIMI never to change anything:
  ```cron
  */15 * * * *  git -C ~/nimi-copies/shop fetch -q origin main && git -C ~/nimi-copies/shop checkout -q --detach origin/main
  ```

## How projects appear in RCAs

People know a repository by its name, not by your local folder, so the RCA:

- names projects by `display_name` (e.g. `acme/shop`; default: the registry key) and files by their path in
  the repo: `acme/shop: services/api/checkout.py:88`. Absolute local paths are rewritten this way before
  anything is posted, so your folder layout never reaches chat;
- links cited code to the file **at the analyzed commit** on the code host (GitHub, GitLab, Bitbucket, and
  GitHub-like hosts). The base URL is `web_url`, or is derived from the copy's `origin` remote (credentials
  in remote URLs are dropped). Without one, citations stay plain text.

```json
"shop": {
  "paths": ["/home/you/nimi-copies/shop"],
  "display_name": "acme/shop",
  "web_url": "https://github.com/acme/shop",
  "sync": "origin/main"
}
```

## Runbooks and alert docs

Runbooks are **hints, never proof**: the skill must find them, label claims from them as unverified,
check their age, and never perform their actions. The best place to document what an alert means is the
project's own repo (e.g. `docs/runbooks/`); list that location in the project file.
