# Dota 2 Pro Match Analysis — Claude Code Context

Public-facing Streamlit dashboard analysing Tier 1 Dota 2 pro match data pulled from the OpenDota API.

**Domain language lives in `CONTEXT.md`** — the glossary, the two data grains, the column dictionary, and the leagues covered. Read it before working on anything that touches metrics or naming. This file is rules and operational facts only.

## Agent skills

### Issue tracker

Specs and issues live as markdown files under `.scratch/<feature-slug>/`, each carrying a `status` and a separate `triage` value in frontmatter, with `.scratch/index.md` as the status board. Closed items move to `.scratch/_done/`. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context — one `CONTEXT.md` plus `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Current Status

- 1,822 matches across 14 Tier 1 tournaments (Oct 2025 – Aug 2026), patches 7.39 / 7.40 / 7.41
- 15 leagues configured — The International 2026 is in `ALL_LEAGUES` but holds no matches until it starts 13 Aug 2026
- Live: https://dota2-pro-match-dashboard-9kymmqtgrymab25ofas4oh.streamlit.app/
- Repo: https://github.com/Wadith-ops/dota2-pro-match-dashboard
- Phases 1–2 shipped; Phase 3 (trend views) planned — see `PLAN.md`

**The coverage gap that motivated this work is closed** (`tier1-pipeline-automation/01`, 2026-08-09): the Esports World Cup 2026 (157 matches) and 1win Essence II (60 matches) are backfilled, and The International 2026 is configured ahead of time so its matches are picked up on the first daily run after it starts. The dashboard now states its own coverage (`02`), so the next gap is visible at the point of use rather than discovered two months later.

**Patch, first blood and game mode are now correct at source** (`04`, 2026-08-09): the CSV carries `patch_label` as a string and `non_captains_mode` as a flag, and an objective at exactly `t=0` no longer blanks its own timing. Four on-the-horn first bloods rejoined the averages; the 155 pre-horn ones were already counted, since nothing ever implemented the filter ADR-0003 reversed.

**Unparsed matches no longer count as zeroes** (`05`, 2026-08-10): every row carries `is_suspect` and `suspect_reason`, the five known matches are out of the figures and still in match history, and a match fetched before OpenDota parsed its replay is held out of the checkpoint and re-fetched for five days. Objective averages rose 0.28% dataset-wide and 2.78% within DreamLeague Season 29 — see ADR-0007.

The underlying cause is not fixed. League coverage is still a hardcoded dict that cannot discover a tournament — the rest of `.scratch/tier1-pipeline-automation/` rebuilds coverage, correctness and hosting. Read ADR-0001 through 0004 before touching the pipeline.

## File Structure

```
project/
├── opendota_pipeline.py      # pipeline shell — network, files, checkpoints, main()
├── liquipedia.py             # Liquipedia client shell — Tier 1 calendar, cache, rate limit
├── core.py                   # pure transform core — plain data in, plain data out
├── dashboard.py              # Streamlit dashboard
├── push_data.py              # bumps dashboard.py date + commits + pushes to trigger redeploy
├── auto_update.py            # scheduled daily run: pipeline, then push if new data
├── requirements.txt          # flexible version ranges — NOT exact pins
├── requirements-dev.txt      # adds pytest; not installed on Streamlit Cloud
├── pytest.ini                # testpaths + pythonpath so `core` imports from root
├── runtime.txt               # python-3.11 for Streamlit Cloud
├── update_log.txt            # auto_update.py append-only log — local only
├── CLAUDE.md                 # this file — rules and facts
├── CONTEXT.md                # domain glossary and model
├── PLAN.md                   # phased roadmap
├── docs/
│   ├── adr/                  # architectural decision records (created lazily)
│   └── agents/               # issue-tracker.md, domain.md
├── .scratch/                 # specs and issues — LOCAL ONLY, gitignored
│   ├── index.md              # status board
│   ├── _done/                # archive — closed items, mirrors the active structure
│   └── <feature-slug>/       # open work only
├── tests/                    # pytest suite — runs offline, no network, no API
│   ├── conftest.py           # fixture loaders + a patch_map fixture
│   └── fixtures/             # gzipped raw OpenDota payloads, committed
├── data/
│   ├── matches.json          # raw match data — LOCAL ONLY, do not commit
│   ├── matches_flat.csv      # flattened data — committed, this is what deploys
│   ├── meta.json             # coverage record — committed, read by the dashboard
│   └── tier1_calendar.json   # Tier 1 fallback calendar — committed, generated not typed
└── checkpoints/
    ├── fetched_matches.json          # pipeline checkpoint — local only
    ├── unparsed_matches.json         # matches given up on as unparsed — local only
    └── liquipedia_tier1_cache.json   # Tier 1 calendar cache — local only
```

## Commands

```bash
python opendota_pipeline.py   # fetch new matches (checkpoint skips already-fetched IDs)
python push_data.py           # commit + push CSV and dashboard, triggers redeploy
python auto_update.py         # both of the above, for the daily schedule
python -m pytest              # full suite, offline, well under a second
```

## Rules

Non-negotiable when writing any new analysis or chart:

- **Never commit `data/matches.json`.** It is gitignored and must stay that way — including after the raw sink is disabled, since it may be re-enabled for modelling.
- **Do not filter `first_blood_time_mins < 0`** — negative values are **valid pre-horn kills**, not artefacts. All 155 fall between −0.9 and −0.1 minutes. This reverses a previous rule; see ADR-0003.
- **Exclude `team_name.isin(["Radiant", "Dire"])`** from every team-level calculation. These are fallback names for anonymous teams, not real teams.
- **Display patch via `patch_label`, never `patch`.** `patch_label` is a string written by the pipeline; `patch` is the same value re-inferred by `read_csv` as a float, which renders `7.4` instead of `7.40`. Read the CSV with `core.CSV_DTYPES` so the label stays a string, and never reformat it — the label is already the name the API gave, and numeric formatting throws on `"Unknown"`. Order labels with `core.patch_sort_key`, not `float()`.
- **Never drop matches to tidy a metric.** Flag them. Silently excluding rows — by `game_mode`, by data quality, by anything — is the class of behaviour that hid a whole tournament for two months. Suspect matches are excluded from *figures* and stay everywhere else: in the CSV, in the match count, and in match history, marked.
- **Compute every figure from `measured(df)`, never from the raw selection.** It drops the suspect rows, whose zeroes mean "unknown" rather than "nothing happened". A new chart, average or probability that reads `filtered` instead of `measured_matches` silently re-admits them. Say what was left out with `note_exclusions()`. See ADR-0007.
- **The Head to Head and Drilldown records count every match; the Tab 1–3 aggregate tables do not.** In the records — matches played, wins, win % — the figure is an outcome, which is right whether or not the replay parsed, and the full match list sits directly below it. In an aggregate table the match count is the denominator of the ten averages beside it, so the whole row is measured-only and the caption states what that cost. Both are deliberate; do not "make them consistent" without reading ADR-0007 first.
- **Distribution charts use match-level rows** (`measured_matches`), never team-perspective rows (`measured_teams`) — the latter double-counts every match.
- **Order tournaments by first match date, descending.** Computed once at startup as `league_start = raw.groupby("league_name")["start_time"].min()` and reused in the sidebar and the Drilldown tab.
- **Note or filter `non_captains_mode`** for anything draft-sensitive; the dataset is overwhelmingly mode 2 (Captain's Mode) and 12 matches are not. The flag is written at source on every row, so filter on it rather than re-deriving from `game_mode` — and say so in the chart when you do.
- **Keep `requirements.txt` on flexible ranges.** Exact pins broke imports on Streamlit Cloud, which defaults to Python 3.14. `requirements-dev.txt` follows the same rule.
- **New transformation logic goes in `core.py`, not the pipeline.** If it takes plain data and returns plain data, it belongs in the core and gets a test. Anything reaching the network, the filesystem, git or Streamlit stays in the shell. This is the seam the whole test suite hangs off — see `docs/adr/0005-pure-transform-core.md`.
- **Nothing in the test suite may touch the network.** Fixtures are recorded payloads under `tests/fixtures/`; the whole suite runs offline in under a second, and it stays that way.
- **Liquipedia's four access conditions are code, not intent.** Any new call to `liquipedia.net` goes through `liquipedia.py` so it inherits the User-Agent, the 30-second `action=parse` interval and the cache. Anything displaying calendar data renders `core.LIQUIPEDIA_ATTRIBUTION`. These are the terms of the free API — see ADR-0006.
- **A Liquipedia page that parses to zero rows is a failed fetch, not an empty Tier 1 list.** Treating it as real would report every tournament missing at once, the first time a class name changes. `get_tier1_events` falls back and reports its `source`; never infer health from an empty list.
- **Closing an issue is three moves, not one:** set `status: done`/`dropped`, move the file to `.scratch/_done/` mirroring its path, and move its row to Closed in `.scratch/index.md` — all in the same turn. See `docs/agents/issue-tracker.md`.

## Dashboard (dashboard.py)

Streamlit + Plotly. 5-tab layout with global sidebar filters.

**Sidebar filters:** League (multi), Team (multi, empty = all), Patch (multi), Side (Radiant/Dire/Both — single team only)

**Coverage line:** a caption between the title and the tabs, stating data date, latest match date, match count, tournament count and excluded count. It renders *before* the empty-filter guard, so it still shows when the filters select nothing.

**Tabs:**
- **1 — Team**: 9-metric KPI row, then Roshan / Kills / Barracks each as grouped bars by patch and by tournament, plus game length histogram and box plot by patch
- **2 — Tournament**: stats table (all 4 metrics per tournament) + 4 comparison bar charts
- **3 — Meta Trends**: per-patch KPI columns + 4 comparison charts (roshans, kills, barracks, game length violin)
- **4 — Head to Head**: two teams; record (matches, wins, win %) over every match, 4 avg stat bar charts (A / B / match total), match history with a `Data` column marking flagged rows, Both Lost Racks % + Both Slew Rosh %, over/under calculator
- **5 — Drilldown**: independent tournament + team filters (at least one required); record/win % if a team is selected, 4 avg stat bar charts, match history with the same `Data` column, same probability stats and over/under calculator as H2H

**Key helpers:**
- `load_data()` — reads the CSV with `core.CSV_DTYPES`, parses `start_time`, fills the blank `suspect_reason` back to `""`, adds the derived columns listed in `CONTEXT.md`. `patch_label` and the quality flags are **not** derived here — they come from the CSV
- `measured(df)` — the rows a figure may be computed from: everything but the suspect matches. `measured_matches` and `measured_teams` are the global selection run through it, and every figure on the page reads one of those; `filtered` and `team_filtered` survive only for match history. `tourn_stats` / `patch_stats` are aggregate frames, a different thing entirely
- `note_exclusions(df)` — the orange caption stating how many matches of a selection the figures leave out. Silent at zero, never silent above it
- `match_history(selection)` / `over_under(selection, avg_label, key_prefix)` — the two blocks Head to Head and Drilldown share. Both take the **whole** selection: `match_history` lists all of it and marks the flagged rows in a `Data` column, `over_under` measures it internally so no caller can price a line off a match with no recorded objectives
- `build_team_perspective(df)` — pivots match rows into one row per team per match
- `by_patch(df)` / `patch_order(df)` — release order for a per-patch frame and for a category list. Both wrap `core.patch_sort_key`; every patch axis in the app comes from one of them
- `load_meta()` — reads `data/meta.json`, returning `{}` when absent or malformed
- `coverage_line(df, meta)` — builds the coverage caption. **Counts come from the CSV, never from `meta.json`**, so the line cannot advertise coverage the loaded page does not have; only `generated_at` and `excluded_count` are read from meta
- `format_date()` — renders `5 Aug 2026`. Does not use `%-d`, which is not portable to Windows
- `load_data()` is `@st.cache_data(ttl=1800)`; `build_team_perspective()` is `@st.cache_data` with no ttl
- **Do not pass a hand-rolled cache key to a cached function.** Streamlit hashes DataFrame arguments unless the parameter name starts with `_`, so the contents are already in the key.
- `ANON_NAMES = {"Radiant", "Dire"}`

## Transform Core (core.py)

Pure functions only — plain data in, plain data out. No network, no filesystem, no clock, no Streamlit. Importing it has no side effects, which is what lets the tests exercise it offline.

- `flatten_objectives(objectives)` — objectives array to counts and timings. A missing or empty array yields zeroes, indistinguishable from "nothing happened"; `suspect_reasons` is what tells them apart.
- `flatten_match(match, patch_map)` — one raw payload to one match row. **`patch_map` is a parameter, not a global** — building it is an API call, and injecting it is what makes this testable. Writes `patch_label` (always a string, `"Unknown"` when the patch is absent), `non_captains_mode`, `is_suspect` and `suspect_reason` alongside the raw values.
- `suspect_reasons(match, objective_counts=None)` — every reason this match's objective data cannot be trusted, as a tuple; empty means the numbers stand. Missing objectives short-circuit, since the zero towers *are* the missing array rather than separate evidence. Pass the counts when you already have them; the verdict is the same either way.
- `retry_window_open(match, now)` — whether a suspect match is young enough to re-fetch. **The deadline hangs off the match's own `start_time`**, not off when the pipeline first saw it, which is what keeps it pure and what makes a backfilled event past its deadline on arrival.
- `classify_fetch(match, now)` — what to do with a match just fetched: `FETCH_COMPLETE`, `FETCH_HELD` or `FETCH_UNPARSED`. The whole re-fetch policy is this one function, and it is tested here rather than in the shell that acts on it.
- `index_by_match_id(matches)` / `store_match(matches, positions, match)` — upsert into the raw sink. A re-fetched match **replaces** its earlier payload; appending a second copy would count the match twice in every average.
- `patch_sort_key(label)` — orders patch labels by release, tolerating lettered names and `"Unknown"`. Used by the dashboard's patch filter.
- `CSV_DTYPES` — the dtypes `read_csv` needs so the CSV round-trips: `patch_label` and `suspect_reason` as `str`. A blank `suspect_reason` still reads back as NaN — dtype cannot fix that, so `load_data()` fills it.
- **Objective timings convert on `is not None`, never on truthiness.** A first blood at exactly `t=0` is a real event, and a pre-horn one is negative; both are lost by an `if raw_time` test.
- `build_rows(matches, patch_map)` — one row per match, order preserved.
- `coverage_meta(rows, generated_at)` — the `meta.json` record. **`generated_at` is passed in** because reading the clock is I/O. Counts come from the rows, where `start_time` is still a unix timestamp; `excluded_count` is how many of them are suspect.

## Data Pipeline (opendota_pipeline.py)

The shell around the core: network, files, checkpoints. Still organised in `# %%` cells:

1. Config, league definitions, `get_patch_map()` and `ensure_directories()` — both **called from `main()`, never at import**
2. Rate-limited fetcher (`fetch_url`), 1s delay, 60 calls/min free tier
3. **3a** checkpoint load/save; **3b** match detail fetcher
4. Main loop, match-level checkpoint only, upserts into `matches.json` via `core.store_match`
5. `build_dataframe(patch_map)` reads the raw JSON, calls `core.build_rows`, exports `matches_flat.csv`, then writes `data/meta.json` via `write_meta()`

**`checkpoint_or_hold()` acts on `core.classify_fetch` and is the re-fetch mechanism.** A suspect match inside its five-day window is deliberately *not* added to `fetched_matches`, so the next run treats it as unfetched and fetches it again; once the window closes it is checkpointed, recorded in `checkpoints/unparsed_matches.json`, and never retried. Each run tallies complete / held / permanently unparsed and prints the three counts. Do not "fix" the missing `add()` — the absence is the feature, and `tests/test_suspect.py::TestClassifyFetch` is what holds it.

**`CORE_FIELDS` must list every field the flat row is built from.** With `SAVE_RAW` off that list *is* the stored payload, so a field missing from it reads downstream as missing from the API.

**The module does nothing when imported.** `main()` is the only entry point and only the `__main__` guard calls it, so `python opendota_pipeline.py` and `auto_update.py` work exactly as before while `import opendota_pipeline` costs nothing. Reintroducing module-level execution breaks `tests/test_pipeline_shell.py`.

**To re-fetch a league's matches** (e.g. objectives were missing): remove those match IDs from `checkpoints/fetched_matches.json` *and* those records from `data/matches.json`, then re-run.

**API:** OpenDota (`https://api.opendota.com/api`), 60 calls/min and 50k/month on free tier. Endpoints: `/leagues/{id}/matches`, `/matches/{id}`, `/constants/patch`.

## Liquipedia Client (liquipedia.py)

Obtains the Tier 1 calendar that defines the dataset's scope. Parsing lives in `core.py`; this is the network, the cache and the clock. **Importing it does nothing** — every entry point takes its collaborators as arguments, which is how the tests run offline.

**API:** `https://liquipedia.net/dota2/api.php`, `action=parse&prop=text` on `Tier_1_Tournaments`. This is the free MediaWiki endpoint and needs **no API key** — not `api.liquipedia.net`, which is the paid v3 product. See ADR-0006.

- `ParseRateLimiter` — one `action=parse` per 30s. Clock and sleep injected. One call per run is far inside the limit; the limiter is for issue 14, which walks several years in one run. **`fetch_tier1_html` defaults to the process-wide `_PARSE_LIMITER`** — building one per call gives each an empty history and enforces nothing, which is how the interval once held in the tests and nowhere else.
- `fetch_tier1_html(page, limiter, get)` — returns HTML or `None`. **Never raises.** A MediaWiki error arrives as HTTP 200 with an `error` key, so the status code alone does not mean success.
- `get_tier1_events(...)` — returns `{"events": [...], "source": ...}`. Degrades fresh cache → network → stale cache → committed seed calendar. **Read the `source`**; an empty list is not a health signal.
- `data/tier1_calendar.json` — the committed fallback, generated from the same table rather than typed. Issue 06's ledger absorbs it.
- The cache is 24h, in `checkpoints/` and local-only.

## Hosting

- Streamlit Community Cloud, redeploys automatically on every push to `master`
- Three data files are committed: `matches_flat.csv`, `meta.json` and `tier1_calendar.json`. `matches.json` and the Liquipedia cache stay local. All three are in the `DEPLOYED` list in `push_data.py` **and** `auto_update.py` — adding a new deployed data file means editing both.
- Streamlit may not redeploy on CSV-only pushes — always use `push_data.py`, which bumps a `# data: YYYY-MM-DD` comment in `dashboard.py` so a `.py` file always changes. The `ttl=1800` on `load_data()` is the safety net.

## Key Technical Decisions

- `SAVE_RAW` — full raw API response saved to `matches.json`, enabling future hero/player extraction. Reads from an env var; defaults `true` locally, set `false` in CI.
- Match-level checkpoint only — league match-ID lists are always re-fetched so new matches are detected.
- Buildings counted via `goodguys`/`badguys` in the `key` field, not the `team` field, which is absent on `building_kill` events.
- Patch IDs mapped dynamically from the OpenDota constants API at the start of `main()`, then passed down as an argument. The mapped name is written twice — as `patch`, which `read_csv` re-infers as a float, and as `patch_label`, the string the dashboard reads. `patch` has no consumer left in this repo; it stays because removing a column from a published CSV is a bigger change than issue `04` asked for, and it is a candidate for deletion, not for use.
- Re-fetching an unparsed match is the *absence* of a checkpoint entry, not a queue. There is no retry ledger, and the deadline lives in the payload — five days after the match's own `start_time` — which keeps the decision pure and makes a backfilled event past its deadline the moment it arrives.
- A correctness fix belongs at source, in the row the pipeline writes, not in each consumer. `patch_label`, `non_captains_mode` and the `is not None` timing conversion all moved there in `04`; the workarounds they replaced were spread across the dashboard and the rules file.
- Transformation is split from I/O at a single seam (`core.py`). The refactor was verified by rebuilding all 1,822 rows through the new core and confirming `matches_flat.csv` came out byte-identical.
- Tests are characterisation tests first: they record what the pipeline does *today*, so the correctness fixes in `tier1-pipeline-automation/04` show up as intended changes rather than silent ones.
- `radiant_towers_lost` / `dire_towers_lost` name buildings *destroyed*, framed as losses by the owner.
- `os.chdir()` removed from the pipeline in favour of `Path(__file__).parent` anchoring.
- **An empty league match list is not a failure.** `fetch_url()` returns `None` on failure and a list on success, so the main loop tests `if data is None`, never `if not data`. A league added before it starts — The International 2026 — legitimately returns `[]`, and truthiness would log it as a failed fetch.
- Tier 1 scope is read from Liquipedia's free MediaWiki API, whose terms explicitly permit it; the paid v3 API was never needed. The four access conditions are enforced in code rather than intended — see ADR-0006. The manually transcribed calendar option survives as the *fallback*, so a markup change costs freshness rather than the whole list.
- Liquipedia and OpenDota name the same tournament differently — `BLAST SLAM VII` against `Blast Slam VII`, `PGL Wallachia Season 7` against `PGL Wallachia 2026 Season 7`. This is why resolution is by date window (ADR-0001) and why the calendar carries dates as its primary key of use.

## Adding New Leagues

A league belongs in the dataset if it is on [Liquipedia's Tier 1 Tournaments list](https://liquipedia.net/dota2/Tier_1_Tournaments) — not if OpenDota calls it `professional`, which 2,468 leagues are. Qualifiers, Division 2 and regional events are out. See ADR-0001.

Current process, until the ledger lands (`tier1-pipeline-automation/06`):

1. Add to the `ALL_LEAGUES` dict in Step 1
2. `ACTIVE_LEAGUES = list(ALL_LEAGUES.keys())` handles the rest
3. Run the pipeline — only new matches are fetched
4. Run `python push_data.py`
5. Add the league to the table in `CONTEXT.md`

To find the OpenDota league id for a Tier 1 event, **match on dates, never on names** — the two sources name tournaments differently. Take the event's date window from Liquipedia and find the league whose matches fall inside it; if more than one does, the right one is the league with the most teams already in the dataset.

The date windows no longer need looking up by hand: `python liquipedia.py` prints the calendar, and `data/tier1_calendar.json` holds it offline. Applying it automatically is the resolver, `tier1-pipeline-automation/08`.

## Environment

Python 3.11.5. Key libraries: requests, pandas, plotly, streamlit.

## Out of Scope for Now

- Match outcome or duration prediction modelling
- Player-level stats (present in raw `matches.json`, not extracted)
- Hero picks/bans analysis (present in raw `matches.json`, not extracted)
