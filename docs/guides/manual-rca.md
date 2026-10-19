# Running an RCA by hand

## In Claude Code (no service needed)

```bash
cd nimi-skill
claude
```

Then paste the alert or log text after `/rca`:

```
/rca [Alerting] Order export delayed
Tables with no new data for 6h: orders, order_items
```

The skill runs in a forked `rca-investigator` subagent under the guard hook (read-only, registered projects
only). It ends with a report and an `RCA_FIELDS` block ([output contract](../reference/output-contract.md)).
History lookups are not available here: they are provided only by the service.

## Through the service

Both commands store the request and result in the database, like automatic RCAs:

```bash
# A stored message, by its provider ID: triage first (may reuse an earlier RCA), then the skill
.venv/bin/python -m nimi_service rca spaces/AAAA…/messages/…
.venv/bin/python -m nimi_service rca spaces/AAAA…/messages/… --fresh   # always a new RCA: no reuse, no exclusion rules
.venv/bin/python -m nimi_service rca spaces/AAAA…/messages/… --no-post # store it, never post it

# Any text (a manual request: no triage, no channel, nothing posted)
.venv/bin/python -m nimi_service rca-text "<alert or log text>"
```

Manual text is scanned for secrets first (a red flag is raised if any are found). Service runs include
[history access](../architecture/rca-skill.md#history-access-nimi-sql) when `NIMI_SKILL_HISTORY=true`.

## Reading the result

To look at a stored RCA again (automatic or manual) without starting a new run:

```bash
.venv/bin/python -m nimi_service show spaces/AAAA…/messages/…          # request + latest report
.venv/bin/python -m nimi_service show spaces/AAAA…/messages/… --chat   # the chat message instead
.venv/bin/python -m nimi_service show spaces/AAAA…/messages/… --render   # rendered now, current layout, nothing left out
.venv/bin/python -m nimi_service show 42 --all                         # by request ID, every attempt
```


The printed result shows outcome, cost, duration, model, origin, confidence and self-healing, followed by
the report. Outcomes:

| Outcome | Meaning |
|---|---|
| `rca` | A report. |
| `not_applicable` | The skill found nothing to analyze (e.g. a resolved message). |
| `insufficient_context` | Not enough signal; the reason says what is missing. |
| `error` / `timeout` | The run failed or was cut short. A report without its closing `RCA_FIELDS` block counts as an error. |
