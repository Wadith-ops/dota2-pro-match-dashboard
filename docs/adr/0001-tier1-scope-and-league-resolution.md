# ADR-0001: Tier 1 scope is defined by Liquipedia, and leagues resolve by date window

**Date:** 2026-08-09
**Status:** Accepted

## Context

League coverage was a hardcoded dict (`ALL_LEAGUES`) that only changed when a human edited it. Between 7 June and 9 August 2026 the daily job reported success every day while two Tier 1 tournaments ran and were never captured: the Esports World Cup 2026 (157 matches, 7–19 July) and 1win Essence II (60 matches, 30 July – 5 August).

We needed a definition of "Tier 1" the pipeline could apply itself. Three candidates were investigated against real data.

**OpenDota's `tier` field does not describe the distinction we care about.** All twelve leagues then tracked are `professional` — a class containing 2,468 leagues. The International is `premium`; the Esports World Cup is `professional`. Neither tier alone selects the dataset, and `professional` is a phone book rather than a filter.

**An organiser allowlist cannot work.** The 2026 professional/premium set contains "DreamLeague Season 29" alongside "DreamLeague Division 2 Season 3", and "PGL Wallachia Season #7 Asia Closed Qualifiers" alongside "PGL Wallachia 2026 Season 7". Name matching admits the qualifiers and the second-tier circuits along with the events we want.

**Team overlap cannot gate.** Scoring a league by the share of matches involving teams already tracked was tested. Qualifiers score *higher* than the events they qualify for — PGL Wallachia S7 Asia Closed Qualifiers scored 87.2% against the Esports World Cup's 74.5% — because Tier 1 teams play in their own qualifiers. The score measures "do teams I track appear here", not "is this Tier 1".

## Decision

**The dataset is Liquipedia's Tier 1 Tournaments list.** Scope is defined by an external, maintained, published authority rather than by a heuristic or a hand-curated dict.

**Leagues resolve to events by date window, never by name.** Liquipedia publishes each event's date range; an OpenDota league's matches fall inside it. Where a window contains more than one candidate league, the candidate with the highest share of matches involving already-tracked teams wins — a *relative* ranking, not an absolute threshold.

**A ledger (`leagues.json`) records every league and its verdict** — `active`, `rejected` or `pending` — so an excluded league is visibly excluded rather than merely absent, and any verdict is reversible.

**The ledger is seeded by existence, not by date or id range.** League ids are not chronological: The International 2013 is id `65006`, above every 2026 league. Any id-range heuristic silently mis-seeds the back catalogue.

## Consequences

Date-window resolution over the 2026 Tier 1 list gives 9 correct mappings from 9 played events. Six windows resolved uniquely; three contained a second league and the overlap tiebreaker picked correctly by margins of 46, 78 and 78 percentage points.

Qualifiers, Division 2 events and regional circuits need no special handling — they fall outside main event windows. This is why the approach survives where name matching and tier filtering do not.

Names are never compared between the two sources, so the naming mismatch between Liquipedia and OpenDota stops being a problem to solve.

An absolute overlap threshold would be wrong: the correct winner in one window scored 85.8%, so any fixed cutoff near that value is arbitrary. Ranking is required.

The tiebreaker is bootstrapped from teams already in the dataset, so it degrades for an event with a wholly unfamiliar field. Ambiguity is surfaced in the approval pull request rather than resolved silently, so such a case is visible rather than silently wrong.

The project takes a dependency on Liquipedia's continued maintenance of that page. Discovery failure is non-fatal by design: the run continues on the existing ledger.

How the list is *obtained* is deliberately left open — their v3 API is a paid product, so the choice is between the MediaWiki endpoint under their terms of use and a manually transcribed calendar, roughly 13 entries a year. This ADR fixes the *definition* of scope and the *resolution method*; the transport is an implementation detail resolved in `tier1-pipeline-automation/07`. The resolver is identical either way.

**Only Liquipedia's tournaments table is authoritative.** The Timeline template on the same page deliberately includes Tier 2 events by the listed organisers — the page states this directly above the table. Parsing the page's wikitext yields the Timeline and produces wrong answers; this is what led us to briefly and incorrectly classify FISSURE Universe Episode 8 as Tier 1 and 1win Essence II as not Tier 1.

## Alternatives rejected

- **OpenDota `tier` gating** — does not separate the dataset from 2,468 other professional leagues.
- **Organiser name allowlist** — cannot distinguish a main event from its own qualifiers or its Division 2 circuit.
- **Team-overlap threshold as the gate** — qualifiers outscore the events they qualify for.
- **Match-count threshold** — EPL Masters 2026 has 132 matches and 0% overlap; volume does not indicate relevance.
