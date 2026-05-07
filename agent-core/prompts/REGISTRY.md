# Prompt Registry

Single landing page for the production prompt library. Edit prompts as
versioned `<id>.md` files in this directory; this registry is regenerated
manually on each library change (or by `tools/prompt_audit.py --update-registry`
in a future iteration).

## Library at a glance

| ID | Version | Owner | Call site | Bench set | Latest score | Audit (2026-05-07) | Last review |
|---|---|---|---|---|---|---|---|
| [intent_classifier](intent_classifier.md) | 1.0.0 | wenwen | `orchestrator.classify_intent` | trace_eval_set | **100%** (12/12) | ok | 2026-05-07 |
| [intent_analyzer](intent_analyzer.md) | 1.0.0 | wenwen | `safety.outer.intent_analyzer.analyze_intent` | outer_safety_smoke_eval | _not yet wired_ | ok | 2026-05-07 |
| [coref_resolver](coref_resolver.md) | 1.0.0 | wenwen | `preprocessing.coref_resolver.make_coref_resolver_node` | coref_eval_set | _not yet wired_ | ok | 2026-05-07 |
| [query_agent](query_agent.md) | 1.0.0 | wenwen | `agents.query_agent._make_route_node` | trace_eval_set | **100%** (12/12) | ok | 2026-05-07 |
| [action_agent](action_agent.md) | 1.0.0 | wenwen | `agents.action_agent._make_route_node` | inner_safety_smoke_eval | **100%** (10/10) | ok | 2026-05-07 |
| [planning_agent](planning_agent.md) | 1.0.0 | wenwen | `agents.planning_agent.make_plan` | trace_eval_set | **100%** (12/12) | warn (accepted) | 2026-05-07 |

Latest full audit report: [`audit_reports/audit_2026-05-07.json`](audit_reports/audit_2026-05-07.json).
Bench history per prompt: [`bench_history/<id>.jsonl`](bench_history/).
The single `warn` on `planning_agent` is an accepted finding — see that
prompt's frontmatter `leakage_check.notes` for the rationale.

The two `_not yet wired_` rows require `ANTHROPIC_API_KEY` and a budget
for real-LLM eval runs (`evaluation/run_coref_eval.py` and
`evaluation/run_outer_safety_eval.py`). The bench runner registers them
explicitly as unwired (exits 2 with reason) rather than silently passing —
unwired ≠ ok.

**Audit column legend**: `pending` = not yet run by `tools/prompt_audit.py`;
`pass` = no overkill or leakage findings; `warn` = minor issues; `fail` =
must-fix before deploy.

## Conventions

- **One prompt per file** under `prompts/<id>.md`.
- **YAML frontmatter** at the top: `id`, `version`, `purpose`, `owner`,
  `call_site`, `model`, `performance.benchmark`, `overkill_check`,
  `leakage_check`, `changelog`. Schema documented in `prompts/__init__.py`.
- **Body is plain text**, loaded by `load_prompt(id)` with frontmatter stripped.
  Templates that require runtime substitution (e.g., `{role}`, `{intent}`)
  must escape literal `{` / `}` as `{{` / `}}`.
- **Edit + bump**: every prompt change increments `version` (semver) and
  appends a `changelog` entry with `change`, `why`, and `eval_delta`.
- **Audit before merge**: `python -m tools.prompt_audit` must pass before
  any prompt PR is merged.

## How to add a prompt

1. Pick an `id` (snake_case, matches file stem).
2. Create `prompts/<id>.md` with frontmatter + body.
3. Replace the inline string at the call site with `load_prompt("<id>")`.
4. Run `pytest` to confirm no regression.
5. Run `python -m tools.prompt_audit` and fix any findings.
6. Add a row to this registry and an entry to `CHANGELOG.md`.

## How to retire a prompt

Set `version: x.y.z` to a final release, add a `changelog` entry with
`change: retired`, and move the file to `prompts/_retired/<id>.md`.
Loader will refuse to load anything from `_retired/`.

## Why this layer exists

See `prompts/__init__.py` module docstring. Short version: prompts are
production artifacts. They deserve the same review, version, audit, and
benchmark discipline as any other code path on a hot request path.
