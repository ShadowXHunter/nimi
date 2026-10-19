# When Claude is unavailable

If a Claude call fails with a usage-limit, rate-limit, credit, budget or login error, the service **pauses
all Claude calls**. The pause is stored in `service_state`, so it survives restarts.

During a pause:

- requests stay in `triage` / `queued`; the failed attempt doesn't count. Ingestion keeps running.
- **one** red flag is raised for the episode (`critical` if an admin must act: credit, budget, login).
- each pending alert's thread gets a short notice, once, that NIMI couldn't analyze it because its Claude
  usage limit was reached. The notice makes no promise of a later RCA, and OK/resolved messages get none.

The pause ends at Claude's reset time if the error includes one, otherwise after
`NIMI_LIMIT_COOLDOWN_MINUTES` (15). Then pending alerts older than `NIMI_STALE_HOURS` are skipped and the
rest are triaged as usual; their RCA is posted in the same thread.

```bash
.venv/bin/python -m nimi_service status    # shows an active pause, its kind and until when
.venv/bin/python -m nimi_service resume    # clear it now (e.g. after topping up credit or logging in)
```

| Error kind | Clears by itself | Needs an admin |
|---|---|---|
| `usage_limit` (subscription limit) | yes, at reset | |
| `rate_limit` | yes | |
| `credit`, `budget` | | yes |
| `auth` (not logged in, invalid key) | | yes |

A report without its closing `RCA_FIELDS` block (cut short by a limit or crash) is an error, never a
finished RCA. "Not logged in" responses that the CLI reports as successful are detected too.
