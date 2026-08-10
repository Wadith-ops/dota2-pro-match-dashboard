# ADR-0007: Suspect matches are excluded from figures, not from the dataset

**Date:** 2026-08-10
**Status:** Accepted
**Implements:** `tier1-pipeline-automation/05`

## Context

OpenDota answers a match request as soon as the match ends, whether or not it has parsed the replay. An unparsed match arrives with its `objectives` array missing entirely, and the flattener turns that absence into zeroes: zero towers, zero barracks, zero Roshans, no first blood. Nothing in the row distinguishes those zeroes from a game where none of it happened.

Five matches in the dataset are in that state, all in DreamLeague Season 29. One is `8821954344` — 96 minutes long, 70–60 on hero kills, and recorded as having destroyed no buildings at all.

Three questions had to be answered together: what counts as suspect, what "excluded" covers, and when to stop trying to fix it.

## Decision

**A match is suspect when its objectives are missing or empty, when both sides lost zero towers in a game over twenty minutes, or when the combined hero kill score is zero.** The verdict is computed at flatten time and written to the serving CSV as `is_suspect` and `suspect_reason`.

**Suspect matches are excluded from figures and kept everywhere else.** They stay in the CSV, in the match count, in the coverage line's `excluded_count`, and in every match history table, marked. They are left out of every average, percentage, distribution and over/under.

**Records of outcomes are not figures.** The Head to Head and Drilldown records — matches played, wins, win % — count every match. Who won is recorded on the match itself and is right whether or not the replay was parsed; dropping a played game from a series record would state something false about a series the user can see listed directly underneath. The aggregate tables in Tabs 1–3 are computed wholly on measured rows, so that a row's match count is the denominator of the averages beside it. Both carry a caption stating what was left out.

**Re-fetching is holding an id out of the checkpoint, and the deadline is the match's own start time plus five days.** A suspect match inside its window is not added to `fetched_matches`, so the next run sees an id it has no record of fetching and fetches it again. Once the window closes it is checkpointed like any other match, recorded in `checkpoints/unparsed_matches.json`, and never retried. A match that parses in the meantime is re-flattened without its flag; no manual step exists to forget.

The three-way verdict lives in `core.classify_fetch`, not in the pipeline. It takes plain data and a clock and returns one of three names, which is the whole policy — the shell only decides that `FETCH_HELD` means "skip the `add()`". Keeping it in the core is what lets the mechanism be tested, including the give-up branch, which will not otherwise execute against real data for as long as OpenDota keeps parsing replays on time.

## Consequences

The five known matches were dragging every objective average down by 0.28% dataset-wide and by 2.78% within DreamLeague Season 29, where they sit. Those figures now move by that much, in the correct direction. Nothing else about the dataset changes: the other 33 CSV columns rebuilt byte-identical.

Anchoring the deadline in the payload rather than in a first-seen ledger keeps the decision pure and needs no state carried between runs. It also gives the right answer for a backfill: an event imported two months after it was played is past its deadline on arrival, which is correct, since its replays were parsed long ago or never. The five known matches are in exactly that position and will not be retried.

A match fetched twice must not be stored twice. `core.store_match` replaces the payload held for an id rather than appending, because a duplicated match would be counted twice in every average — the very fault this work removes, arriving through the mechanism meant to fix it.

`CORE_FIELDS` gained `radiant_score`, `dire_score` and `game_mode`. With `SAVE_RAW` off, that list *is* the stored payload, and the scores were not in it — so every trimmed match would have flattened to a zero kill score and been flagged as suspect. The bug predates this ADR and had no effect while `SAVE_RAW` defaulted on; it would have fired on the first CI run.

## Alternatives considered

**Drop suspect matches from the CSV.** Rejected. Silently excluding rows is what hid the Esports World Cup for two months, and a match that was played is a fact about the tournament even when its objectives are unknown.

**Impute the missing objectives.** Rejected. There is nothing to impute from, and a plausible-looking invented number is worse than a stated absence for someone pricing a bet.

**Anchor the retry deadline on when the pipeline first saw the match.** Rejected. It requires a ledger of first-seen timestamps carried between runs, and it gets backfills wrong by giving a two-month-old unparsed replay five fresh days of retries.

**Mark "permanently unparsed" on the row rather than in a checkpoint.** Rejected. Whether a match is still being retried is a fact about the pipeline's state on a given day, not about the match, and putting it in the CSV would mean handing the flattener a clock. The checkpoint file answers the question the mark is for — "is this still zeroed because nobody has retried it, or because retrying stopped?" — for the one person who asks it.

**Report every criterion a match fails.** Partly rejected: a match with no objectives array also has no towers in it, and reporting both dresses one fact up as two. Missing objectives short-circuit; the tower and kill rules are reported independently of each other, because they are independent signals.

## Notes

The classification is deliberately narrow. A rule that flags a fifth of the dataset would be worse than none, so the criteria were checked against the whole CSV before being adopted: they select exactly five matches out of 1,822, and no complete match in the fixtures trips them.
