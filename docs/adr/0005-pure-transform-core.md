# ADR-0005: Transformation is separated from I/O at a single seam

**Date:** 2026-08-09
**Status:** Accepted

## Context

`opendota_pipeline.py` ran in full at import. Reaching the `import` statement fetched the patch constants, looped every league fetching match details, and rebuilt the CSV — as a side effect. Nothing could import a function from it without triggering a multi-hour API run, so nothing in the pipeline had ever been tested.

That mattered beyond tidiness. `tier1-pipeline-automation` changes what the pipeline computes: patch labels, first-blood handling, game-mode flagging, suspect-match exclusion, league resolution, Standard extraction. Every one of those is a change to a number the dashboard displays, and there was no way to demonstrate a change did what was intended and nothing else.

The repository had no tests at all, so the layout chosen here is the convention the rest of the work follows.

## Decision

**One seam: a pure transform core in `core.py`.** Functions there take plain data and return plain data. Network access, file reads and writes, checkpointing, the clock, git and Streamlit rendering all stay outside it, in `opendota_pipeline.py` and `dashboard.py`.

Two consequences fall out of "no I/O", and both are deliberate:

- `flatten_match` takes `patch_map` as an argument rather than reading a module global, because building that map is an API call.
- `coverage_meta` takes `generated_at` as an argument, because reading the clock is I/O and a function that reads it cannot be asserted against a fixed value.

**The pipeline module does nothing when imported.** `main()` is the only entry point, called only under a `__main__` guard.

**Tests assert on returned data, never on call ordering or internal structure**, and never touch the network. Fixtures are recorded raw OpenDota payloads, gzipped and committed.

## Consequences

The suite runs offline in under a second, so there is no reason to skip it.

Tests written now are **characterisation** tests: they pin what the pipeline produces today, including behaviour known to be wrong — `flatten_match` still blanks a timing of exactly zero, and that is asserted. Issue 04 will change those assertions deliberately. A characterisation test that quietly encodes a bug is only safe when the bug is labelled as one at the assertion, so each carries the issue that owns the fix.

The refactor was verified beyond the unit tests by rebuilding all 1,822 rows through the new core: `matches_flat.csv` came out **byte-identical** to the committed file, and `meta.json` differed only in its timestamp.

`tests/test_pipeline_shell.py` fails if import-time execution returns. Its stubs raise a `BaseException` rather than an `Exception` deliberately, because the shell catches `Exception` broadly and would swallow an ordinary failure — the test would then pass against exactly the code it exists to catch. The same stubs stop a regression from making the *test suite* fetch every league for real; that is not hypothetical, it happened while verifying these tests against the pre-refactor module and took 133 seconds.

Only the transforms that exist today moved into the core — flattening and coverage meta. Quality classification, league resolution and Standard extraction arrive with issues 05, 07/08 and 10 respectively. Stubbing them now would fix their interfaces before the requirements are known.

Dead code went with the refactor: `get_league_match_ids()` was never called, its checkpoint logic having been inlined into the main loop.

## Notes

The network shell and the Streamlit rendering layer stay explicitly untested — they are verified by running them, not by unit tests. The same will apply to the GitHub Actions workflow when it lands in issue 13; there is no `.github/workflows/` directory yet.
