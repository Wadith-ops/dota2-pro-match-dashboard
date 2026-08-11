# ADR-0010: What a Standard record holds, and how the store is written

**Date:** 2026-08-11
**Status:** Accepted

## Context

ADR-0002 decided the split — one fetch, two artifacts, with the raw sink retained as a disabled seam — and named the tiers in prose: "objectives in full, picks and bans, per-player aggregates, teamfight summaries, per-player benchmarks, and the radiant gold and XP advantage curves", at "roughly 20 KB against a raw 386 KB".

Implementing it turned that prose into three questions with real numbers behind them, and the answers are not derivable from the ADR that asked them.

Measured over 80 matches of the actual store, a raw payload averages **288 KB**, not 386. `players` is 89% of it and `teamfights` 8%; `objectives`, the only part the dashboard reads, is 1%. A record that kept every player field except the timeseries came to **38 KB** — 69 MB for the current 1,822 matches, and past GitHub's 100 MB per-file limit inside two seasons. The 20 KB in ADR-0002 was not free.

## Decision

**The field lists are allowlists, in `core.py`.** `STANDARD_MATCH_FIELDS`, `STANDARD_PLAYER_FIELDS` and `STANDARD_TEAMFIGHT_FIELDS` name what is kept; everything else is dropped. A denylist would let a field OpenDota adds tomorrow into a file that is committed and grows for ever, and the cost of an allowlist — a re-fetch to widen it — is one the split already accepts.

**Every field the flat row is built from is on the match allowlist.** The CSV is built from Standard records, so a field missing from that list reads downstream as missing from the API. This is the same failure `CORE_FIELDS` had, in the same place, and it is now held by a test that flattens each recorded fixture twice and compares.

**A teamfight is kept as `start`, `end`, `last_death` and `deaths`.** The per-player breakdown OpenDota nests inside each fight — gold, XP and damage deltas per player per fight — is 6 KB of a 26 KB record, a quarter of the store for a breakdown that is not what "teamfight summaries" names.

**Benchmark percentiles are rounded to four decimal places.** They arrive as `0.9521044992743106`; eighteen characters of a number nobody can use is 8% of the store. Rounded rather than dropped — the benchmarks themselves are an acceptance criterion.

The result is **20.1 KB per match, 37.5 MB for 1,822 matches**, against the 20 KB and 35 MB ADR-0002 estimated.

**The store is JSON Lines, appended, and read last-write-wins.** One record per line; a new match is one `write` and one `fsync` of its own bytes, with nothing read and nothing held in memory. A match fetched twice — every suspect match is, for five days — is written twice, and the last line for a match id wins when the store is read, in first-seen order. That is exactly what the in-memory upsert it replaces did, moved to the read side, which is what lets the write side stay an append.

**Documents get atomic writes; logs get appends.** The ledger, the coverage record, the resolution report and the checkpoints are rewritten whole, so they are written to a temporary file and renamed over the original. The Standard store and the raw sink are only ever appended to.

**The raw sink keeps the same line-per-match shape**, and `SAVE_RAW` now defaults to off. Turning it on is a configuration change, as ADR-0002 requires, and it inherits the append rather than the rewrite it would otherwise have inherited at 14x the size.

## Consequences

The store is 37.5 MB and grows about 4 MB per tournament, so it has roughly two seasons before the 100 MB per-file limit is a live question. Splitting it by season is the obvious answer and is not needed yet.

Writing a match now costs the size of that match. The old shape rewrote the whole document every ten matches: backfilling 217 matches onto an 885 MB store performed about 24 full rewrites — **21 GB of disk writes to add 84 MB** — and the cost was *total dataset × new matches*, so it degraded on every run regardless of how few matches arrived.

Per-fight player deltas and the players' ability build are not retained. Both are re-fetchable, which is the standing bargain of the whole split.

An interrupted append can leave one damaged line. `core.parse_standard_records` counts and skips those, the run says so, and the match is recovered by clearing it from the checkpoint. Refusing to read the other ten thousand lines over one would be the expensive answer.

Reading the legacy 1 GB file needed a streaming JSON array reader (`core.iter_json_array`), because `json.load` needs the document and its parsed form in memory together — which is precisely why that file could never be processed anywhere but the machine that wrote it.

The split was verified the way the two before it were: all 1,822 matches were extracted, the flat rows were rebuilt from the Standard store, and `matches_flat.csv` came out **byte-identical**.

## Alternatives rejected

- **Keep the per-player teamfight deltas.** 25.5 KB a match and 47.5 MB today, hitting the file limit within a season, for detail the criterion did not name.
- **Gzip the store.** Concatenated gzip members append cleanly and would cut it to about 7 MB, and git would still delta it. Rejected because an opaque blob in a repository is a thing nobody can inspect, and the size problem it solves is two seasons away.
- **Denylist the fat fields instead.** Smaller diff, and it hands the size of a committed file to whatever OpenDota adds next.
- **A JSON array rewritten atomically.** Keeps the document readable whole and keeps every byte of the quadratic write cost the append was for.
