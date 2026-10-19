# Changing the skill

The skill is plain files in `nimi-skill/.claude/`. Changes take effect on the next run; the service
records a hash of the skill files (`skill_version`) with every result, so results can be compared across
versions.

## Where to change what

| To change | Edit |
|---|---|
| The investigation process, report rules, output contract | `skills/rca/SKILL.md` |
| The report layout | `skills/rca/templates/report.md` |
| Allowed sources (the list the skill follows) | `skills/rca/sources.md` |
| Which repos exist and how to pick one | `skills/rca/projects/` ([Adding a project](../guides/adding-a-project.md)) |
| What is technically allowed | `hooks/nimi_guard.py` |
| The subagent's model and tools | `agents/rca-investigator.md` |

Keep paths portable: refer to the skill's own files as `${CLAUDE_SKILL_DIR}/…` in `SKILL.md` (Claude Code
substitutes it), and to project paths as `<name>/…` resolved from `registry.json`.

## Changing the guard

The guard is the security boundary, so:

- keep it an allowlist that **fails closed** (any exception denies);
- prefer narrowing over widening; when widening, add tests in `nimi-service/tests/` that cover both the
  newly allowed form and near-misses that must stay denied;
- give denials actionable reasons (the model reads them) and mark the model's own formatting slips as
  `low` severity so they don't page anyone;
- if the output contract changes, update `rca.py` (`parse_output`) and
  [the reference](../reference/output-contract.md) together.

## Testing a change

1. Unit tests: `.venv/bin/python -m pytest -q tests`.
2. By hand: `cd nimi-skill && claude`, `/rca <a real alert with a known cause>`.
3. Through the service on a scratch database: `rca-text "<alert>"` (includes history access and the
   session-wide guard).
4. Check `~/.local/state/nimi/guard-denials.jsonl` for unexpected denials.

When choosing models, compare correctness and cost on alerts with confirmed root causes. The subagent
defaults to Sonnet: on such comparisons it has matched larger models on correctness at a fraction of the
cost, but measure with your own alerts before changing it.
