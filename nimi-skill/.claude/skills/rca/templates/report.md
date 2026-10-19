# RCA: <short title>

**Alert:** <alert name / first error line>
**Service:** <service>  **Environment:** <env>
**Project analyzed:** <project name> @ <commit> — <why this project>
**Incident window:** <start> – <end> UTC

**Self-healing:** <yes — re-run by <mechanism, file:line> | no | unknown> 
**Runbook:** <file § section (last updated <date> UTC), or "none found — searched for <terms>">

## Summary
<2–3 sentences: what broke, why, and impact.>

## Root cause
**Origin:** <internal (code/config/infra/deploy) | upstream (source system / third-party SDK or API / data) | undetermined> — <one line why>

<The underlying cause, with evidence citations.>

## Trigger
<What happened that set it off (deploy, config change, source change, SDK/API behaviour change, data change, traffic spike, dependency failure, ...). If it is not determined, list the checks that would tell an internal trigger from an upstream one.>

## Timeline
All times in UTC. Where the source used another offset, show it: `<original with offset> → <HH:MM> UTC`.
| Time (UTC) | Original time (if not UTC) | Event | Source |
|---|---|---|---|
| | | | |

## Related history
<Related messages found around the incident (channel, time UTC, one line each, cited as [label](msg:<message_id>)), how often this signature has fired and since when, and earlier RCAs ([RCA #<id>](rca:<id>), conclusion, feedback) — and whether this RCA agrees with them. "history unavailable" or "none found" if so.>

## Evidence
- <source>: <finding>

## Sources checked
| Source | Checked? | Finding |
|---|---|---|
| | | |

## Open questions
<Anything unresolved, including leads that pointed outside the allowed sources.>

## Confidence
<High / Medium / Low — and why.>

## Suggested next steps
<Remediation / follow-ups, citing runbook sections where they apply. Suggestions only — the skill does not take action.>
