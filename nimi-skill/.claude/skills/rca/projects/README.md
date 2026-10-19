# Projects

The projects (code repositories) the `/rca` skill may read, and how it picks one, live in **`local/`** in
this folder. `local/` is git-ignored: it describes *your* systems.

Set it up from the template:

```bash
cp -r example local
```

Then edit:

| File | Purpose |
|---|---|
| `local/registry.json` | Each project's **absolute** checkout path(s). The guard hook reads it: only these paths (plus the skill folder) are readable, and `git -C` may only target them. Missing or invalid = nothing is readable (fail closed). |
| `local/INDEX.md` | How to pick a project from an alert or log: the signals that identify each project, the default, what to do when nothing matches. |
| `local/<name>.md` | One guide per project: where to orient, where code / config / dependencies live, runbook locations, project-specific notes. Write the project's path as `<name>`; the skill resolves it from the registry. |

Without `local/INDEX.md` the skill replies `insufficient_context` ("no projects configured").

See the documentation: *Guides → Adding a project to analyze*.
