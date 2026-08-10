# ADR-0008: The league ledger and the Tier 1 calendar stay separate files

**Date:** 2026-08-10
**Status:** Accepted

## Context

The feature spec expected one file. Written before the Liquipedia transport was settled, it described the fallback Tier 1 list as "a manually transcribed calendar **in the ledger**, roughly 13 entries a year", and `CLAUDE.md` carried the same expectation: "Issue 06's ledger absorbs it."

By the time the ledger was built, two things had changed. Issue 07 had landed, so the calendar was no longer 13 hand-typed entries — it is 324 events across 2005–2027, generated from Liquipedia's rendered tournaments table and refreshed by `python liquipedia.py --refresh-seed`. And seeding by existence (ADR-0001) turned out to mean the ledger holds every league OpenDota knows about: 10,050 entries, about a megabyte.

## Decision

`data/leagues.json` and `data/tier1_calendar.json` remain two files.

They hold two different objects with two different keys, from two different sources, changing on two different schedules:

| | `leagues.json` | `tier1_calendar.json` |
|---|---|---|
| Unit | OpenDota **league** | Liquipedia **event** |
| Source | `api.opendota.com/api/leagues` | Liquipedia MediaWiki API |
| Key of use | league id | date window |
| Written by | the pipeline, every run | `liquipedia.py --refresh-seed`, by hand |
| Holds | this project's verdict | an external fact |

The join between them — which league is which event — is the resolver, `tier1-pipeline-automation/08`. It writes its answer into the ledger's `tier1_event` field, which is why that field exists and is `null` on every row today.

## Consequences

A verdict is this project's opinion and an event window is Liquipedia's fact. Keeping them apart means a bad merge, a markup change or a rejected pull request can spoil one without touching the other, and it keeps the CC BY-SA attribution attached to exactly the data it covers (ADR-0006).

The daily run rewrites the ledger whenever it discovers a league. Folding a hand-curated calendar into that file would put an automated writer and a human editor in the same document — and the calendar's own rule is that it is only worth replacing when someone has looked at what changed.

The cost is two files where the spec anticipated one, and a resolver that has to open both. That is the smaller cost: they were never the same list.

This reverses a decision recorded in the spec, so it is written down here rather than left as a line in `CLAUDE.md`. Nothing in ADR-0001 or ADR-0006 depends on the two lists sharing a file.

## Alternatives rejected

- **One file holding both.** Puts an automated writer and a human editor in the same document, and attaches Liquipedia's licence terms to a file that is mostly OpenDota's data.
- **Calendar entries as ledger rows with a `pending` verdict.** A Tier 1 event with no OpenDota league is a *known gap*, not an undecided league; collapsing the two would lose the distinction `CONTEXT.md` draws, which is the one the Upcoming tab (`09`) is built on.
