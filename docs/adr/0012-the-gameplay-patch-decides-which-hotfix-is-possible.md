# ADR-0012: The gameplay patch decides which hotfix a match can be on

**Date:** 2026-08-12
**Status:** Accepted
**Relates to:** ADR-0001 (two sources naming the same thing differently), `tier1-pipeline-automation/16`

## Context

`patch_label` records the gameplay patch — `7.40` — because that is all OpenDota reports. Its `/constants/patch` has 61 entries and every name is a plain `d.d`; hotfixes are not in it. A match played on 7.40c was recorded as `7.40`, and three patch buckets carried the whole 1,822-match dataset.

Valve's own patch list (`dota2.com/datafeed/patchnoteslist`) has 117 entries covering 7.08 to the present, every lettered revision included, each with a `patch_timestamp`. Every match carries `start_time`, so the hotfix is derivable: the latest patch released at or before the match started.

Derived that way, the hotfix agrees with OpenDota's own patch id on **98.46%** of matches. All 28 disagreements fall in a single nine-hour window on **2025-12-15**, the day 7.40 released, all of them DreamLeague Season 27. Valve's timestamp puts them on 7.40; OpenDota puts them on 7.39.

This is a second external source of truth disagreeing with the first, which is the kind of decision ADR-0001 exists for. Two facts settle it.

**Every one of Valve's 117 timestamps is exactly midnight America/Los_Angeles** — 07:00 UTC through the summer, 08:00 UTC through the winter, tracking US daylight saving on both sides. The field looks like a release moment and is a release *date*. It cannot place a match on either side of a release that happened during its own day, and Valve ships patches in the Pacific afternoon and evening.

**OpenDota's constants carry the release to the second, and `patch_label` is derived from them.** 7.40 is dated `2025-12-16T00:50:40Z` — 16:50 Pacific on the 15th, nearly seventeen hours after Valve's timestamp for the same patch. Deriving the gameplay patch from those dates alone reproduces OpenDota's own `patch` id on **1,822 of 1,822** matches, so `match.patch` is not independent evidence about the client: it is the same `start_time` derivation against a finer table.

The 28 disputed matches were played between 02:55 and 11:48 Pacific on release day. They were played on 7.39.

## Decision

**`patch_label` decides the gameplay patch. Valve's list only chooses the revision within it.**

`core.resolve_hotfix(start_time, patch_label, releases)` takes the latest Valve release at or before the match started *whose gameplay patch is the one `patch_label` names*. Where no revision of that patch has been released yet, the answer is `patch_label` itself — a match on 7.41 with nothing lettered out is on 7.41, never on the previous patch's last hotfix and never on nothing.

Two fallbacks, both narrow. A label that is not a patch name — `"Unknown"` — is not a claim about anything, so there is nothing to hold the answer inside and Valve's list is read unconstrained; it is the only evidence there is and better than none. A match older than every release in the list resolves to nothing, because it cannot be placed.

**The hotfix is a new column, not a replacement.** `patch_label` is unchanged and no figure on the dashboard moves.

## Consequences

`patch_hotfix` and `patch_label` **cannot contradict each other**, by construction rather than by luck: the second is derived inside the first. Across the rebuilt CSV, `gameplay_patch(patch_hotfix) == patch_label` on 1,822 of 1,822 rows, against 1,794 under the literal reading.

Three patch buckets become nine, four of them under 65 matches:

| Gameplay patch | Matches | Hotfix | Matches |
|---|---|---|---|
| 7.39 | 605 | 7.39e | 605 |
| 7.40 | 516 | 7.40 | 33 |
| | | 7.40c | 483 |
| 7.41 | 701 | 7.41 | 56 |
| | | 7.41a | 22 |
| | | 7.41b | 119 |
| | | 7.41c | 260 |
| | | 7.41d | 184 |
| | | 7.41e | 60 |

That is why it is a second column. The dashboard's primary output is over/under percentages, and a 22-match bucket produces figures that read as precise and are not. The gameplay patch stays the headline comparison axis where the samples are large; the hotfix is the finer lens.

The rule is **only ever exercised on release day**, and only when matches are played in the gap. 7.41 released while nobody was playing — the last 7.40 match ended 2026-03-23 22:09 UTC and the first 7.41 match started 2026-03-24 11:59 UTC, with the boundary somewhere between — so the whole decision rests on one observed window. `tests/test_hotfix.py::TestTheBoundaryCostsTwentyEightMatches` holds that window against a recorded fixture of the 28 matches, so changing the rule shows up as a changed number.

**A patch list that cannot be fetched never blanks a column that already has values.** The CSV is rebuilt whole from the Standard store every run, so a failed fetch would otherwise write an empty column over a full one. `core.carry_forward_hotfix` fills from the CSV about to be replaced wherever this run resolved nothing — it fills and never corrects, so a rule change can still move an answer. Verified end to end: with the fetch stubbed to fail, the rebuilt CSV came out byte-identical across all 1,822 rows.

## Alternatives considered

**Read Valve's timestamp literally.** Simplest, one source, and wrong on 28 matches — which would leave `patch_hotfix` claiming 7.40 in rows where `patch_label` says 7.39. Two columns of the same file contradicting each other is a defect somebody trips over later, and the evidence says the label is the one that is right.

**Re-anchor Valve's release table to OpenDota's constant dates**, overriding the timestamp of any release both sources name. Produces exactly the same answer on every match in the dataset. Rejected because it needs the constants' `date` field threaded through a second seam to reach the same place `patch_label` already is, and because it would silently do nothing the day OpenDota stops publishing dates.

**Keep the gameplay patch out of it and record both derivations.** A column recording that two sources disagree is a column nobody can group by.

## Notes

Neither source is wrong about its own subject. Valve publishes patch notes and dates them by day; OpenDota records when the client changed. The mistake available here was reading one as if it were the other, and it is available again the next time a finer grain is wanted from a second feed.
