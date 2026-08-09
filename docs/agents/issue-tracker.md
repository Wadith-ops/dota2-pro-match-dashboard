# Issue tracker: Local Markdown

Specs and issues for this repo live as markdown files under `.scratch/`. There is no external tracker — do not reach for `gh issue`, Jira, or Linear.

## Layout

```
.scratch/
├── index.md                          ← derived status board (see "The index contract")
├── _done/                            ← archive; mirrors the structure below it
│   └── <feature-slug>/
│       ├── spec.md
│       └── issues/
│           └── 01-<slug>.md
└── <feature-slug>/                   ← active work only
    ├── spec.md                       ← the feature spec
    └── issues/
        ├── 01-<slug>.md              ← one file per ticket, numbered from 01
        └── 02-<slug>.md
```

- One directory per feature, named with a kebab-case slug.
- Issues are **one file per ticket** — never a single combined tickets file.
- Numbering restarts at `01` inside each feature. A ticket is referenced as `<feature-slug>/03`, regardless of which side of `_done/` it currently sits on.
- **The top level of `.scratch/` holds open work only.** Anything closed lives under `_done/`. See "Archiving".

## Frontmatter

Every `spec.md` and every issue file opens with a frontmatter block. It is the source of truth for that item's state.

```markdown
---
id: 03
feature: hero-picks
title: Add hero pick/ban tab
status: todo
triage: ready-for-agent
blocked-by: 01, 02
created: 2026-08-09
updated: 2026-08-09
---
```

- `id` — `spec` for a spec file, the zero-padded number for an issue.
- `blocked-by` — comma-separated ids within the same feature; **omit the key entirely** when nothing blocks it. Never write `blocked-by: none`.
- `created` / `updated` — `YYYY-MM-DD`. Bump `updated` on every edit to the file.
- Body follows the frontmatter: the ask, acceptance criteria, then a `## Comments` heading where conversation appends chronologically.

## The two axes

`status` and `triage` are orthogonal and must never be collapsed into one field. An item always carries both.

### `status` — how far along is it?

| Value | Meaning | Set it when |
|---|---|---|
| `todo` | Agreed, not started | The file is created. This is the default. |
| `in-progress` | Actively being worked | Before your first edit to any implementation file, not after. |
| `done` | Finished and verified | Tests pass, or Wade has confirmed the behaviour. Never on "I think it works". |
| `dropped` | Abandoned, kept for the record | Add a one-line reason under an `## Outcome` heading. |

`done` and `dropped` are **terminal** — reaching either one triggers an archive move. See "Archiving".

Do not invent values outside this set. If an item is waiting on something, it stays `todo` or `in-progress` and carries a `blocked-by` key.

### `triage` — should we pick it up, and who by?

| Value | Meaning |
|---|---|
| `needs-triage` | Not yet routed. The default on creation. |
| `needs-info` | Blocked on an answer from Wade — ask the question under `## Comments`. |
| `ready-for-agent` | Claude can pick this up unattended. |
| `ready-for-human` | Needs Wade — a judgement call, credentials, or a manual step. |
| `wontfix` | Routed out. Pair with `status: dropped`. |

A new item is therefore `status: todo` + `triage: needs-triage`.

## Archiving

The top level of `.scratch/` is the working set. `_done/` is the archive. Keeping the two separated is what makes the active folder scannable, so the move is part of closing an item, not a tidy-up to do later.

**The trigger.** The moment you set `status: done` or `status: dropped` on a file, move it. Same turn, before you report the work finished. There is no state in which a closed file sits at the top level.

**Where it goes.** `_done/` mirrors the active structure exactly — same feature slug, same `issues/` subdirectory, same filename:

```
.scratch/hero-picks/issues/03-add-tab.md
        → .scratch/_done/hero-picks/issues/03-add-tab.md
```

Create the mirrored directories as needed. Never renumber, never rename, never edit the body on the way across — the path under `_done/` must be reconstructible from the feature slug and id alone.

**Closing a whole feature.** Move issue files individually as each closes. When the feature's last open item closes, move `spec.md` across too and delete the now-empty active directory, so the feature disappears from the top level entirely.

**Resolving a reference.** To find `<feature-slug>/<id>`, look under `.scratch/<feature-slug>/` first, then `.scratch/_done/<feature-slug>/`. A `blocked-by` id that resolves to a file under `_done/` with `status: done` is satisfied; one resolving to `status: dropped` is **not** automatically satisfied — surface it to Wade rather than assuming the blocker evaporated.

**Reopening.** Set the status back to `todo` or `in-progress`, move the file back to the active tree, bump `updated`, and move its index row from Closed to Active. Reopening is a real transition, not an edit in place.

## The index contract

`.scratch/index.md` is a **derived view**. The frontmatter in each file is authoritative; the index is a convenience board so Wade can see the queue at a glance. It is maintained by hand, which means these rules are load-bearing:

1. **Write the file first, mirror second.** Change the frontmatter, then update the index — in that order, in the same turn.
2. **Update it in the same turn as the change.** Creating an item, changing `status`, changing `triage`, or adding `blocked-by` all require an index edit before you report the work done. Never defer it to a later turn or a later session.
3. **Move, don't delete.** When `status` becomes `done` or `dropped`, move the row from **Active** to **Closed** with the outcome and date. Rows are never removed from the index. A row in Closed means the file lives under `_done/`; the two must agree.
4. **Bump `_Last updated:_`** to today's date on every edit.
5. **Reconcile on read.** The first time you open `index.md` in a session, spot-check it against the files under `.scratch/` **and `.scratch/_done/`**. If they disagree, the frontmatter wins — correct the index silently before continuing, then carry on with the task. A closed file still sitting at the top level is drift: finish the archive move as part of the reconcile.

If you cannot update the index for some reason, say so explicitly in your reply rather than leaving it stale in silence.

## When a skill says "publish to the issue tracker"

Create a file under `.scratch/<feature-slug>/` (creating the directory and `issues/` as needed), with full frontmatter, then update `.scratch/index.md`.

## When a skill says "fetch the relevant ticket"

Read the file at the referenced path, falling back to `_done/` per "Resolving a reference". Wade will normally pass the path or a `<feature-slug>/<id>` reference directly. If only a feature is named, read its `spec.md` and list its `issues/` — check both the active tree and `_done/`, since a partially finished feature has files in both.
