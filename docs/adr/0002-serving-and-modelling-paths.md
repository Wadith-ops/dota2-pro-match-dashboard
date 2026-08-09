# ADR-0002: Split the serving path from the modelling path, and keep the raw sink as a seam

**Date:** 2026-08-09
**Status:** Accepted

## Context

`SAVE_RAW` stored the complete OpenDota response for every match in `data/matches.json`, on the reasoning that hero and player data would be useful later. That file reached **928 MB** for 1,605 matches. The pipeline loaded it whole, appended to it, and rewrote it in full — with `indent=2` — every ten matches and again after each of twelve leagues, then parsed it a second time to build the CSV.

Two forces made this untenable. Moving the pipeline to GitHub Actions is impossible while it depends on a file that cannot exist on a hosted runner and cannot go in the repository. And the write pattern is a corruption risk: a mid-write interrupt leaves a file the next run cannot parse, with no atomicity and no backup.

Profiling one match showed where the bytes are. Of 386 KB, `players` is **86.9%** and `teamfights` a further 9.7%. `objectives` — the only thing the dashboard reads — is **1.4%**.

The stated future intent is match-outcome modelling. That intent is real, so simply discarding everything was not obviously right.

## Decision

**Two artifacts from one fetch.** The pipeline shape becomes: fetch the full payload, optionally write it to the raw sink, extract a Standard record, produce the serving row.

**The serving artifact** is the flat CSV the dashboard reads, plus data-quality flags.

**The modelling artifact** is a **Standard** record per match, roughly 20 KB against the raw 386 KB, committed to the repository — approximately 35 MB for the current dataset, growing about 4 MB per tournament. It retains objectives in full, picks and bans, per-player aggregates, teamfight summaries, per-player benchmarks, and the radiant gold and XP advantage curves. It discards the per-player timeseries — lane positions, damage matrices, purchase logs — which are the 87%.

**The raw sink is retained as a disabled seam, not deleted.** Enabling full-fidelity capture later must be a configuration change, not a rewrite.

**The existing `matches.json` is extracted once locally, then deleted.** No cold archive is kept.

## Consequences

The modelling asset becomes versioned, CI-writable, and independent of one machine — which is what the whole exercise was for.

Full-fidelity data for new matches is **not retained**. Widening the schema later means re-fetching from OpenDota, not re-reading a local archive. This is accepted: OpenDota retains pro matches indefinitely and a full back-catalogue re-fetch is roughly 30 minutes. Recoverability therefore depends on OpenDota, not on anything held locally.

The gold and XP advantage curves cost about 0.5 KB each and are the highest-value-per-byte features in the payload for outcome modelling. They are the specific reason Standard was chosen over a leaner tier that kept only final stats.

The pruning refactor carries a real risk of welding the raw sink shut. Keeping the seam live is an explicit acceptance criterion, not an implementation detail — "start keeping full payloads again" must stay a config change.

This decision is what unblocks running in CI at all; the hosting migration is downstream of it rather than independent.

## Alternatives rejected

- **Hybrid — serving in CI, raw kept locally.** Re-anchors the modelling store to one machine, which is the problem being solved.
- **External object store (R2, S3, HuggingFace Datasets).** Full fidelity with no repo bloat, but requires an account, a secret and plumbing for a benefit that re-fetchability already provides.
- **Drop the raw payload entirely with no modelling store.** Defensible, since everything is re-fetchable — but the Standard record is cheap and having features accumulate from today is worth more than the 35 MB.
- **Fat tier (~50 KB/match, including purchase logs and per-minute arrays).** The point at which size stops being free, and the tier most cheaply re-fetched if ever needed.
