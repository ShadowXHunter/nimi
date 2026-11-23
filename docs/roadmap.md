# Roadmap

## Planned

- **Interactive RCA** (phase 2 of [comments](design/comments.md)): on-call talks to NIMI in the thread,
  NIMI asks for read-only checks it can't run itself and confirms the cause from the answers.
- **A NIMI Chat app** for posting and `@NIMI` mentions (see the [design](design/comments.md#the-nimi-chat-app)).
- **Read-only production data for the skill:** e.g. query results from a read replica or BI tool, through a
  guarded tool with query checks, row caps and timeouts, so RCAs can confirm hypotheses against real data.
- **More chat providers:** Slack and Microsoft Teams, behind the existing provider interface.
- **Configurable reply templates**, beyond the disclaimer setting.
- **Packaging:** a service unit / container image and a `pyproject.toml`.
- **Triage evaluation:** replay stored history through triage to compare models and tune rules.
- **Mitigation:** possibly, later, carefully scoped automatic mitigation for well-understood alerts.
  Today NIMI only suggests.

## Known limitations

- Local checkouts may differ from what is deployed; the skill records the commit it analyzed.
- The skill can't see production logs, metrics or databases; it reasons from the alert, history, code and
  runbooks.
- One process, one host: fine for a team's alert volume, not built for high availability.
- Google Chat is the only provider so far.
