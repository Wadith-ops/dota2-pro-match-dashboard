# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

This is a **single-context** repo — one `CONTEXT.md` and one `docs/adr/` directory, both at the root.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root — the glossary and domain model.
- **`docs/adr/`** — read the ADRs that touch the area you're about to work in.

If `docs/adr/` doesn't exist yet, **proceed silently**. Don't flag its absence and don't suggest creating it upfront. The `/domain-modeling` skill creates ADRs lazily, when a decision actually gets made.

## File structure

```
/
├── CONTEXT.md          ← glossary + domain model
├── docs/
│   ├── adr/            ← architectural decision records
│   │   └── 0001-<slug>.md
│   └── agents/         ← agent configuration (this file, issue-tracker.md)
└── *.py
```

## Use the glossary's vocabulary

When your output names a domain concept — an issue title, a refactor proposal, a hypothesis, a test name, a chart label, a column name — use the term as `CONTEXT.md` defines it. Don't drift to synonyms the glossary explicitly avoids.

This matters more than usual here because the same word means different things at different grains: a "kill" is a hero kill, not a Roshan kill or a building kill, and *barracks* counts are recorded as *lost* (by the owner) but often displayed as *destroyed* (by the opponent). Getting the term wrong silently inverts a metric.

If the concept you need isn't in the glossary, that's a signal — either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than quietly overriding it:

> _Contradicts ADR-0003 (match-level checkpoint only) — but worth reopening because…_
