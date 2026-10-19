# Glossary

| Term | Meaning |
|---|---|
| **Channel** | A registered chat space/channel. Only channels added with `--rca` get RCAs. |
| **Top-level message** | A message that starts a thread. Only top-level bot messages are analyzed; thread replies are stored as context. |
| **Backfill** | History pulled on a channel's first poll (or with `backfill`). Never triggers RCAs. |
| **Watermark** | The newest message time seen per channel; the next poll starts there. |
| **Signature** | The parser's coarse "kind of message" key, shared by every occurrence of the same alert (e.g. all "Disk usage high" alerts): volatile parts (numbers, IDs, timestamps, URLs, Kubernetes pod suffixes) removed so repeats of the same alert match, also across pod restarts. Used for history, reuse candidates and exclusion rules. |
| **Platform profile** | A YAML file in `nimi-service/parsers/` describing a platform's log layouts, item names to mask, and triage notes. |
| **Layout** | One log line format in a profile; the first layout that matches a message decides its service, logger and title. |
| **Subject** | One affected item a message is about: a table, service, deployment, job, or `*`. Triage decides reuse per subject. |
| **Request** | One RCA decision for one message (or manual text), with a status. |
| **Triage** | The cheap, tool-less model call that decides reuse / new / not applicable. |
| **Reuse** | Answering a repeat of an ongoing incident with an earlier RCA instead of a new one. |
| **Exclusion rule** | A code rule that skips RCA for matching messages before triage. |
| **Stale** | Older than `NIMI_STALE_HOURS` when NIMI can process it; gets no RCA. |
| **Held** | A request with a critical secret in it, kept from all models until `release`. |
| **Red flag** | A recorded problem for an admin: sensitive data, a rule violation or an error. |
| **Pause** | Claude calls stopped because of a usage, credit or login problem. |
| **Guard hook** | The PreToolUse hook that enforces what the skill may do. |
| **Project** | A registered repository the skill may read. |
| **Runbook** | On-call documentation in a project; hints for the skill, never proof. |
| **Origin** | Whether the root cause is internal or upstream. |
| **Self-healing** | Whether a retry at some level re-runs the failed work automatically. |
| **Skill reader** | The read-only Postgres role used by the skill's history SQL. |
