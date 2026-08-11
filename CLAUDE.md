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
- 15 leagues marked `active` in `data/leagues.json` — The International 2026 is one of them but holds no matches until it starts 13 Aug 2026
- Live: https://dota2-pro-match-dashboard-9kymmqtgrymab25ofas4oh.streamlit.app/
- Repo: https://github.com/Wadith-ops/dota2-pro-match-dashboard
- Phases 1–2 shipped; Phase 3 (trend views) planned — see `PLAN.md`

**The coverage gap that motivated this work is closed** (`tier1-pipeline-automation/01`, 2026-08-09): the Esports World Cup 2026 (157 matches) and 1win Essence II (60 matches) are backfilled, and The International 2026 is configured ahead of time so its matches are picked up on the first daily run after it starts. The dashboard now states its own coverage (`02`), so the next gap is visible at the point of use rather than discovered two months later.

**Patch, first blood and game mode are now correct at source** (`04`, 2026-08-09): the CSV carries `patch_label` as a string and `non_captains_mode` as a flag, and an objective at exactly `t=0` no longer blanks its own timing. Four on-the-horn first bloods rejoined the averages; the 155 pre-horn ones were already counted, since nothing ever implemented the filter ADR-0003 reversed.

**Unparsed matches no longer count as zeroes** (`05`, 2026-08-10): every row carries `is_suspect` and `suspect_reason`, the five known matches are out of the figures and still in match history, and a match fetched before OpenDota parsed its replay is held out of the checkpoint and re-fetched for five days. Objective averages rose 0.28% dataset-wide and 2.78% within DreamLeague Season 29 — see ADR-0007.

**Coverage is a ledger, not a dict** (`06`, 2026-08-10): `data/leagues.json` holds all 10,050 leagues OpenDota knows about, 15 `active` and the rest `rejected`, and the pipeline fetches the active ones. A league OpenDota adds after the seed date is recorded `pending` on the next run, so a new tournament arrives as a line in a diff rather than as nothing at all. Changing a verdict is a one-word edit that takes effect on the next run.

**The resolver closes the loop** (`08`, 2026-08-11): every pipeline run reads Liquipedia's Tier 1 calendar and works out which OpenDota league each event is, by date window and never by name. The answer is written as `tier1_event` on the ledger record and per event in `data/tier1_resolution.json`, where an entry with no league is a known gap. Over the 2026 list it reproduces the design session exactly — six windows with one candidate, three contested and correctly ranked, four gaps — and `tests/test_resolver.py::TestThe2026Season` holds that against 78 recorded leagues. See ADR-0009.

**Coverage state is on the dashboard** (`09`, 2026-08-11): a sixth tab reads `data/tier1_resolution.json` and shows the Tier 1 events with no league — marked Overdue once the event has started, which is the case that means something is broken — and the leagues awaiting a verdict, with the evidence that resolved them. It is read-only by design and carries the Liquipedia attribution whether or not either list has rows.

What is still missing is the last mile: a resolved league that nobody has approved is an `ATTENTION` line in the run's output and a row on that tab, not yet a pull request. That is `15`. The rest of `.scratch/tier1-pipeline-automation/` rebuilds correctness and hosting. Read ADR-0001 through 0004 before touching the pipeline, and ADR-0009 as well before touching the resolver.

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
│   ├── leagues.json          # the league ledger — committed, edited in PRs
│   ├── matches_flat.csv      # flattened data — committed, this is what deploys
│   ├── meta.json             # coverage record — committed, read by the dashboard
│   ├── tier1_calendar.json   # Tier 1 fallback calendar — committed, generated not typed
│   └── tier1_resolution.json # event → league, and the gaps — committed, generated
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
python -m pytest              # full suite, offline, about two seconds
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
- **Nothing in the test suite may touch the network.** Fixtures are recorded payloads under `tests/fixtures/`; the whole suite runs offline in about two seconds, and it stays that way. It was under one until the 2026 candidate pool arrived — 78 leagues and 29,000 matches, and worth the second. The page-sized fixtures are **session-scoped** in `conftest.py`; making them function-scoped again costs four seconds.
- **Liquipedia's four access conditions are code, not intent.** Any new call to `liquipedia.net` goes through `liquipedia.py` so it inherits the User-Agent, the 30-second `action=parse` interval and the cache. Anything displaying calendar data renders `core.LIQUIPEDIA_ATTRIBUTION`. These are the terms of the free API — see ADR-0006.
- **A Liquipedia page that parses to zero rows is a failed fetch, not an empty Tier 1 list.** Treating it as real would report every tournament missing at once, the first time a class name changes. `get_tier1_events` falls back and reports its `source`; never infer health from an empty list.
- **Coverage is `data/leagues.json`, and every league in it carries a verdict.** Never reintroduce a hardcoded league list, and never remove an entry to stop fetching a league — set its verdict to `rejected`, which is the record that it was considered. Deleting the entry makes it `pending` again on the next run, and a league that keeps reappearing as a candidate is a decision that was never taken.
- **Tournaments resolve by date window. Never compare the two sources' names, anywhere, for any purpose.** Liquipedia says `PGL Wallachia Season 7` where OpenDota says `PGL Wallachia 2026 Season 7`, and no normalisation fixes that in general. A candidate is a league whose matches *all* fall inside the event window — which is also the only reason qualifiers are excluded, so a rule that names them is a rule that has misunderstood the design. See ADR-0001.
- **Team overlap and window coverage rank candidates; neither ever gates them.** There is no minimum score for either. Qualifiers outscore the events they qualify for; the correct winner scored 85.8% on teams in one real window; and a tournament resolving on the day of its first match covers one day in twelve. Adding a threshold to either re-breaks what ADR-0001 was written to prevent, and would trade a wrong answer for no answer.
- **Tier 1 events nest inside one another, so a window can hold the right league and a smaller wrong one.** FISSURE PLAYGROUND 2 ran entirely inside BLAST Slam IV's 2025 window; both are 100% tracked teams, and the nested one played *more* matches. Coverage is what parts them. Never rank on match count before coverage.
- **The resolver never changes a verdict.** It writes `tier1_event` and nothing else. Recognising a tournament is the pipeline's job; deciding to cover it is Wade's, taken by merging a pull request (`15`).
- **The Upcoming tab is read-only, and no amount of convenience changes that.** No approve button, no write credential, no repository token — the app is public and Streamlit Cloud deploys it from git onto an ephemeral filesystem, so a button that set a verdict would mean a repository write token in a public app. Approval is merging the pull request, which is one tap on a phone. See `tier1-pipeline-automation/09`.
- **Liquipedia attribution renders whenever the Upcoming tab does, including when both its lists are empty.** It credits the source of the Tier 1 list, not the rows, so it must not be tied to a table that can be empty. This is a condition of the free API, not a courtesy — see ADR-0006.
- **`tier` may prune the candidate pool and may not do anything else.** `excluded` and `amateur` are dropped before ranking, because that is OpenDota saying a league is not a competitive event. `professional` says nothing — 2,468 leagues carry it — so it must never select, rank, or break a tie. See ADR-0009.
- **Closing an issue is three moves, not one:** set `status: done`/`dropped`, move the file to `.scratch/_done/` mirroring its path, and move its row to Closed in `.scratch/index.md` — all in the same turn. See `docs/agents/issue-tracker.md`.

## Dashboard (dashboard.py)

Streamlit + Plotly. 6-tab layout with global sidebar filters.

**Sidebar filters:** League (multi), Team (multi, empty = all), Patch (multi), Side (Radiant/Dire/Both — single team only)

**Coverage line:** a caption between the title and the tabs, stating data date, latest match date, match count, tournament count and excluded count. It renders *before* the empty-filter guard, so it still shows when the filters select nothing.

**The empty-selection guard is deliberately split from its `st.stop()`.** The warning renders above the tabs, then `st.tabs(...)` is built and **Upcoming is drawn**, and only then does the page stop. Tabs 1–5 are all computed from the selection and have nothing to draw when it is empty; Upcoming reads none of it, and it is the tab that answers the question an empty page provokes. Rejoining the two would delete coverage state — and the Liquipedia attribution with it — from exactly the moment it is most wanted. This is why `with tab6:` appears before `with tab1:` in the file.

**Tabs:**
- **1 — Team**: 9-metric KPI row, then Roshan / Kills / Barracks each as grouped bars by patch and by tournament, plus game length histogram and box plot by patch
- **2 — Tournament**: stats table (all 4 metrics per tournament) + 4 comparison bar charts
- **3 — Meta Trends**: per-patch KPI columns + 4 comparison charts (roshans, kills, barracks, game length violin)
- **4 — Head to Head**: two teams; record (matches, wins, win %) over every match, 4 avg stat bar charts (A / B / match total), match history with a `Data` column marking flagged rows, Both Lost Racks % + Both Slew Rosh %, over/under calculator
- **5 — Drilldown**: independent tournament + team filters (at least one required); record/win % if a team is selected, 4 avg stat bar charts, match history with the same `Data` column, same probability stats and over/under calculator as H2H
- **6 — Upcoming**: coverage state, read straight out of `data/tier1_resolution.json` and computed from none of the selection. Known gaps (marked **Overdue** or **Upcoming**, the glossary's two words and not synonyms of them; overdue also called out in an error banner above the table), then the leagues awaiting a verdict with their match count, team overlap and contested-window mark, then the Liquipedia attribution. The `Review` link is the repository's open pull requests and says so — it becomes *the* proposing pull request when `15` records a URL on the record

**Key helpers:**
- `load_data()` — reads the CSV with `core.CSV_DTYPES`, parses `start_time`, fills the blank `suspect_reason` back to `""`, adds the derived columns listed in `CONTEXT.md`. `patch_label` and the quality flags are **not** derived here — they come from the CSV
- `measured(df)` — the rows a figure may be computed from: everything but the suspect matches. `measured_matches` and `measured_teams` are the global selection run through it, and every figure on the page reads one of those; `filtered` and `team_filtered` survive only for match history. `tourn_stats` / `patch_stats` are aggregate frames, a different thing entirely
- `note_exclusions(df)` — the orange caption stating how many matches of a selection the figures leave out. Silent at zero, never silent above it
- `match_history(selection)` / `over_under(selection, avg_label, key_prefix)` — the two blocks Head to Head and Drilldown share. Both take the **whole** selection: `match_history` lists all of it and marks the flagged rows in a `Data` column, `over_under` measures it internally so no caller can price a line off a match with no recorded objectives
- `build_team_perspective(df)` — pivots match rows into one row per team per match
- `by_patch(df)` / `patch_order(df)` — release order for a per-patch frame and for a category list. Both wrap `core.patch_sort_key`; every patch axis in the app comes from one of them
- `load_json(path)` — a committed side file, or `{}` when absent or malformed. `load_meta()` and `load_resolution()` are the two named readers over it. **A missing side file is never why the dashboard is down** — the page degrades to what the CSV can tell it
- `format_window(start, end)` — an event's date window as `13 Aug 2026 – 23 Aug 2026`. `Dates TBD` when Liquipedia published none
- `window_verdict(record)` — whether a pending league was its window's only candidate or won a contested one. A contested window is where the resolver *judged* rather than found the one league that fitted, so it is the row worth a second look
- `upcoming_tab()` — the whole of tab 6. A report with no events is reported as **not knowing**, never as "no gaps"; the second is a claim, and a false one
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
- `active_leagues(ledger)` — `{league_id: name}` for the leagues to fetch. **The name is the ledger's, not OpenDota's**: it is written onto every match row as `league_name` and grouped on by the dashboard, so an upstream rename — `SLAM IV` where the dataset has said `Slam IV` for 96 matches — must not split a season into two tournaments.
- `seed_ledger(api_leagues, active_names, seeded_on)` — builds the ledger. **Seeding is by existence**: every league in the response is decided here, `active` or `rejected`, and nothing is seeded `pending`. Never by id range — The International 2013 is id `65006`, above every 2026 league.
- `merge_discovered_leagues(ledger, api_leagues)` — returns `(ledger, discovered)`. Ids the ledger has never seen become `pending`; existing entries are copied through untouched, verdict and name alike.
- `ledger_problems(ledger)` / `verdict_counts(ledger)` — what the run prints about the ledger it loaded. A hand-edited verdict can be misspelled, and a misspelling fails the way a rejection does, so it is reported rather than left silent.
- `format_ledger(ledger)` — the file as text, **one league per line**. `json.dump(indent=...)` would turn 10,050 entries into 50,000 lines and a verdict change into a five-line diff.
- `summarise_leagues(matches, catalogue=None)` — flat match rows to one summary per league: window, match count, team slots. Takes rows in the shape **both** `/leagues/{id}/matches` and `/proMatches` return, which is what lets the shell shortlist from one and confirm from the other. `team_slots` is two entries per match, not one per distinct team, and an anonymous team keeps its slot as `None`.
- `candidates_in_window(summaries, start, end, grace_days=2)` — the leagues whose matches *all* sit inside a window. Drops `NON_COMPETITIVE_TIERS` first. An event with no published window has no candidates — Liquipedia writes "TBD", and an unscheduled event must not take one at random.
- `team_overlap(summary, known_teams)` / `known_team_ids(rows)` — the tiebreaker and the set it is measured against. **A share of slots, not of matches**: no other measure produces 85.8% from 60 matches, which is what pins it to the recorded result.
- `window_coverage(summary, start, end)` — how much of an event's window the league actually spans. It exists because **Tier 1 events nest inside each other**: FISSURE PLAYGROUND 2 ran entirely inside BLAST Slam IV's 2025 window, both leagues score 100% on teams, and match count picks the nested one. Like overlap it **ranks and never gates** — a tournament resolves on the day of its first match, covering one day in twelve.
- `resolution_walk_start(events, grace_days=2)` — the timestamp a pro match walk must reach, at midnight UTC so whether an event resolves cannot depend on what time the job ran.
- `resolve_tier1_events(events, summaries, known_teams)` — one resolution per event, in the order given. Ranks on overlap, then coverage, then match count, then league id, so a level window resolves the same way every run.
- `apply_resolutions(ledger, resolutions)` — writes `tier1_event` onto winners, returns `(ledger, changes)`. **Never touches a verdict, and never clears a mapping** — a run that walked back a fortnight must not wipe the answers of one that walked back a year.
- `events_awaiting_resolution(events, ledger, today, lookback_days=365)` — the events worth spending calls on: started, not already mapped, and inside the lookback. This is what decides how far back the shell walks, and dropping any of the three conditions is expensive.
- `tier1_resolution_state(events, ledger, resolutions=None, previous=None)` — one record per event for `data/tier1_resolution.json`. **The mapping comes from the ledger, not from `resolutions`**, so an event resolved months ago does not regress to a gap on a run that never looked at it. `previous` is the last report's records and is what keeps a contested window's candidates alive: an event is examined exactly once, so without it the evidence would be blanked the day after it was recorded.
- `coverage_gaps(records, today)` / `awaiting_verdict(records)` — the two lists the Upcoming tab renders, read back out of the report the resolver wrote. `coverage_gaps` marks each gap `GAP_UPCOMING` or `GAP_OVERDUE` against the event's own start date, **inclusive** — matches are played on day one — and sorts overdue first, then soonest. `awaiting_verdict` reads its match count and overlap from the **winning candidate's own row**: `candidates` is ranked, not keyed, so the first entry is the winner only by coincidence.
- `SETTLED_VERDICTS` — `active` or `rejected`, and shared by `resolution_problems` and `awaiting_verdict` rather than restated in each. The queue printed in the run's `ATTENTION` lines and the queue shown on the dashboard are the same queue, and a misspelled verdict is on the awaiting side of the line in both: it fails the way a rejection does, so it must be raised rather than treated as decided.
- `resolution_problems(ledger, resolutions)` / `resolution_report(records, generated_at)` — the `ATTENTION` lines and the file. A resolved league nobody has judged is the whole feature in one line. Two things are deliberately silent: a **`rejected`** league, because "never proposed again" is what a verdict is for, and a **gap**, because an unstarted tournament printed as a fault every run is how a real one stops being read. A league claimed by two events *is* reported — `apply_resolutions` keys by league id, so the second claim would quietly evict the first.

## Data Pipeline (opendota_pipeline.py)

The shell around the core: network, files, checkpoints. Still organised in `# %%` cells:

1. Config, paths, `get_patch_map()` and `ensure_directories()` — both **called from `main()`, never at import**
2. Rate-limited fetcher (`fetch_url`), 1s delay, 60 calls/min free tier; **2b** the ledger — `read_ledger` / `write_ledger` / `fetch_all_leagues` / `load_ledger`; **2c** the resolver — `fetch_pro_matches` / `confirm_candidates` / `resolve_tier1_leagues` / `write_resolution`
3. **3a** checkpoint load/save; **3b** match detail fetcher
4. Main loop over the active leagues, match-level checkpoint only, upserts into `matches.json` via `core.store_match`
5. `build_dataframe(patch_map)` reads the raw JSON, calls `core.build_rows`, exports `matches_flat.csv`, then writes `data/meta.json` via `write_meta()`. Returns **`(df, rows)`** — the resolver needs the rows, where team ids are still ints rather than the floats-beside-NaN the frame re-reads them as

**`main()` fetches `/leagues` once and hands it to both callers.** `load_ledger(api_leagues)` takes it as a parameter, and `_FETCH_LEAGUES` is the sentinel that keeps "the caller passed nothing" apart from "the caller passed None because the fetch failed" — collapsing those would make a failed fetch trigger a second one, and a seeded ledger written over a real one.

**The resolver walks `/proMatches` backwards, then confirms each candidate in full.** The walk is cheap and gives windows *and* team ids in one pass, but a league that began before the walk did looks narrower than it is — and narrow is the direction that makes a league fit inside an event it has no business winning. So `confirm_candidates()` re-reads every shortlisted league's whole match list before it can win, and a league that cannot be re-read is dropped rather than judged on a partial window. A short walk therefore costs a missed candidate, recorded as a gap, never a wrong answer.

**Resolution runs last in `main()` and usually costs nothing.** `core.events_awaiting_resolution` skips events the ledger already maps, events that have not started, and events older than `core.RESOLVER_LOOKBACK_DAYS` (365) — so in steady state there is no walk at all. When a tournament opens, it resolves on the day of its first match.

**The lookback is load-bearing, not a tidy-up.** Liquipedia's calendar reaches back to 2005 and this project has never mapped most of it, so *every* one of those events is literally awaiting resolution. Without the horizon the first run walks twenty years of pro matches, and every run after it walks back to the oldest thing it still could not resolve. Auditing the back catalogue is `14`, deliberately separate. `PRO_MATCHES_MAX_PAGES` (400) is the second stop, on the walk itself.

**A 429 is retried; every other bad status is not.** It is the one status that says "ask again", and the resolver is what made it matter — its walk is a hundred-odd consecutive calls, and the first real run of it was refused on every request that followed. `DELAY_SECONDS` is 1.1, not 1.0: the free tier allows 60 calls a minute and the sleep starts *after* the response, so exactly one second sits on the limit rather than inside it. Making the whole fetcher survive network failures is `12`; this is the narrow part the resolver could not ship without.

**`write_resolution()` writes only when the answer changes.** The file is committed and pushed and appears in `auto_update.py`'s change check; rewriting it every run would put a commit in front of Wade every morning carrying a fresh timestamp and no news. `generated_at` is therefore when the answer was *reached*, not when it was last confirmed.

**`max_pages` on `fetch_pro_matches` resolves at call time, not as a default argument.** A module constant bound as a default binds once, when the function is defined, so a reassigned cap is honoured by nothing — the same rule the path parameters in `liquipedia.py` follow, and `tests/test_resolver_shell.py` is what holds it.

**`load_ledger()` is the whole coverage decision, and it has four cases.** No ledger: seed one from OpenDota's league list with nothing active, and fetch nothing this run — which leagues to cover is a judgement, and a pipeline that guessed would be the hardcoded dict again. A ledger that will not parse: fetch nothing and **write nothing**, because every verdict in that file survives a syntax error and none of it survives being written over. No league list: fetch what the ledger already says; discovery going quiet must never stop match fetching. Both: record anything new as `pending` and rewrite the file **only if that changed something**, because it is 10,000 lines and this runs daily. `tests/test_ledger_shell.py` holds all four.

**An empty `/leagues` response is a failure, which is the opposite of the rule the match loop follows.** A league's *match* list is legitimately `[]` before the event starts, so that loop tests `is None`. The *league* list is not — OpenDota has 10,050 — so the seed path tests `not api_leagues`. Seeding on an empty list writes a ledger holding nothing, and the next run greets every real league as newly discovered. `fetch_all_leagues()` likewise rejects a non-list payload: OpenDota can answer 200 with an error object, and a dict passed on to the ledger iterates as its own keys.

**`checkpoint_or_hold()` acts on `core.classify_fetch` and is the re-fetch mechanism.** A suspect match inside its five-day window is deliberately *not* added to `fetched_matches`, so the next run treats it as unfetched and fetches it again; once the window closes it is checkpointed, recorded in `checkpoints/unparsed_matches.json`, and never retried. Each run tallies complete / held / permanently unparsed and prints the three counts. Do not "fix" the missing `add()` — the absence is the feature, and `tests/test_suspect.py::TestClassifyFetch` is what holds it.

**`CORE_FIELDS` must list every field the flat row is built from.** With `SAVE_RAW` off that list *is* the stored payload, so a field missing from it reads downstream as missing from the API.

**The module does nothing when imported.** `main()` is the only entry point and only the `__main__` guard calls it, so `python opendota_pipeline.py` and `auto_update.py` work exactly as before while `import opendota_pipeline` costs nothing. Reintroducing module-level execution breaks `tests/test_pipeline_shell.py`.

**To re-fetch a league's matches** (e.g. objectives were missing): remove those match IDs from `checkpoints/fetched_matches.json` *and* those records from `data/matches.json`, then re-run.

**API:** OpenDota (`https://api.opendota.com/api`), 60 calls/min and 50k/month on free tier. Endpoints: `/leagues`, `/leagues/{id}/matches`, `/matches/{id}`, `/constants/patch`, `/proMatches`. **`/explorer` is not used by anything scheduled** — it answers the whole candidate-pool question in one arbitrary-SQL call and was used by hand, once, to record `tests/fixtures/league_matches_2026.json.gz`. A daily job depending on it is one schema change from breaking.

## Liquipedia Client (liquipedia.py)

Obtains the Tier 1 calendar that defines the dataset's scope. Parsing lives in `core.py`; this is the network, the cache and the clock. **Importing it does nothing** — every entry point takes its collaborators as arguments, which is how the tests run offline.

**API:** `https://liquipedia.net/dota2/api.php`, `action=parse&prop=text` on `Tier_1_Tournaments`. This is the free MediaWiki endpoint and needs **no API key** — not `api.liquipedia.net`, which is the paid v3 product. See ADR-0006.

- `ParseRateLimiter` — one `action=parse` per 30s. Clock and sleep injected. One call per run is far inside the limit; the limiter is for issue 14, which walks several years in one run. **`fetch_tier1_html` defaults to the process-wide `_PARSE_LIMITER`** — building one per call gives each an empty history and enforces nothing, which is how the interval once held in the tests and nowhere else.
- `fetch_tier1_html(page, limiter, get)` — returns HTML or `None`. **Never raises.** A MediaWiki error arrives as HTTP 200 with an `error` key, so the status code alone does not mean success.
- `get_tier1_events(...)` — returns `{"events": [...], "source": ...}`. Degrades fresh cache → network → stale cache → committed seed calendar. **Read the `source`**; an empty list is not a health signal.
- `data/tier1_calendar.json` — the committed fallback, generated from the same table rather than typed. It stayed its own file rather than being absorbed into the ledger as the spec expected: the calendar is Liquipedia's list of *events*, keyed by date window, and `data/leagues.json` is OpenDota's list of *leagues* with this project's verdict on each. Joining them is the resolver, `tier1-pipeline-automation/08`. See ADR-0008.
- The cache is 24h, in `checkpoints/` and local-only.

## Hosting

- Streamlit Community Cloud, redeploys automatically on every push to `master`
- Five data files are committed: `matches_flat.csv`, `meta.json`, `tier1_calendar.json`, `leagues.json` and `tier1_resolution.json`. `matches.json` and the Liquipedia cache stay local. All five are in the `DEPLOYED` list in `push_data.py` **and** `auto_update.py` — adding a new deployed data file means editing both.
- `auto_update.py` exits early when nothing changed, and **the ledger and the Tier 1 resolution both count as change alongside the CSV**. A run that fetched no matches but discovered a new league has found the one thing this job exists to notice; testing the CSV alone would leave that `pending` entry dirty in the working tree until somebody happened to look. The resolution file is safe to test on because it is only rewritten when the mapping itself moves.
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
- The ledger holds **all 10,050 leagues**, not just the interesting ones, because "decided" has to be recorded for every id in existence — otherwise every run reports thousands of leagues as newly discovered. That is what makes the file a megabyte, and why it is written one league per line: a verdict change is then a one-line diff in a pull request, which is where the file is reviewed.
- The ledger's `tier1_event` is written by the resolver and by nothing else. It was seeded `null` on every row rather than guessed, because guessing it by name is the one thing ADR-0001 rules out.
- Resolution is bootstrapped from the dataset it is building: the tiebreaker measures overlap against teams already present. That degrades for an event with a wholly unfamiliar field, which is theoretical on a 43-team closed circuit — and an ambiguous window is surfaced rather than resolved silently, so such a case would be visible rather than silently wrong.
- The resolver runs **after** the fetch, not before. Its answer informs the next run, which is the right way round: a league it has newly recognised still needs a verdict before anything is fetched from it, and running it first would only mean ranking candidates against a dataset one run staler.
- Team overlap is a share of **team slots**, two per match. This was settled by reproduction rather than by taste: neither per-match measure can produce the recorded 85.8% from 1win Essence II's 60 matches, and the slot measure reproduces five of the six recorded figures to the decimal.
- The design session's 22.1% for Road To EWC 2026 Regional Qualifiers does not reproduce — it measures 19.1%. Every other figure matches exactly and the winner is unaffected (it loses to 100% either way), so the recorded number is treated as a slip. If a future change makes it 22.1% again, that is a sign the *measure* has changed, not that a bug was fixed.
- The 2026 resolution fixture is 78 leagues and 29,000 matches, trimmed to the four fields the resolver reads. It is the whole 2026 candidate pool rather than a shortlist, so the date window is shown doing the excluding — including of the two `excluded`-tier leagues that would otherwise make two more windows ambiguous.
- **Reproducing 2026 exactly is not proof the resolver is right.** No two Tier 1 events overlap in 2026, so the whole acceptance criterion is silent on nested windows — the case that mismapped BLAST Slam IV on the first live run. The 2025 nesting is now a test of its own. Assume the same of the next criterion: a year that happens not to contain a case does not rule it out.
- The worst case for API spend is a started event that never resolves: it stays awaiting for a year and every run walks the full 259 pages looking for it. Affordable daily, not comfortable at the six-hourly cadence `13` introduces — the fix is a shorter horizon on how long a *gap* keeps being retried, and it belongs with that change.
- The ledger was seeded with the *curated* league names from the retired `ALL_LEAGUES` dict, not OpenDota's. `league_name` is written onto every match row and grouped on by the dashboard, so seeding "SLAM IV" over "Slam IV" would have split that tournament in two the next time a match was fetched.
- Replacing the dict with the ledger was verified by running the full pipeline and confirming `matches_flat.csv` came out byte-identical across all 1,822 rows.

## Adding New Leagues

A league belongs in the dataset if it is on [Liquipedia's Tier 1 Tournaments list](https://liquipedia.net/dota2/Tier_1_Tournaments) — not if OpenDota calls it `professional`, which 2,468 leagues are. Qualifiers, Division 2 and regional events are out. See ADR-0001.

**The resolver finds the league id; a human decides whether to cover it.** Since `08`, a run that meets a new Tier 1 event resolves it to a league and says so:

```
ATTENTION: 'Esports World Cup 2026' resolved to league 19785 (Esports World
Cup 2026), whose verdict is 'pending' — nobody has decided whether to cover it
```

That line, and `data/tier1_resolution.json`, are where the league id comes from. Do not go looking it up by hand, and **never match on names** — the two sources disagree on them by design.

1. Find the league's entry in `data/leagues.json` — every league OpenDota knows about is already in there, `rejected` or `pending`
2. Change its `verdict` to `active`, and set `name` to the name the tournament should carry in the dashboard
3. Run the pipeline — only new matches are fetched
4. Run `python push_data.py`
5. Add the league to the table in `CONTEXT.md`

Step 2 is the whole change. Nothing else needs editing, and the reverse — `active` back to `rejected` — works the same way and takes effect on the next run. Turning step 2 into a merged pull request is `tier1-pipeline-automation/15`.

If the resolver reports a **gap** — a Tier 1 event with no league at all — there is nothing to activate. Either the event has not started, or OpenDota has no data for it; both are recorded and neither is an error. `python liquipedia.py` prints the calendar if the window needs checking by eye.

## Environment

Python 3.11.5. Key libraries: requests, pandas, plotly, streamlit.

## Out of Scope for Now

- Match outcome or duration prediction modelling
- Player-level stats (present in raw `matches.json`, not extracted)
- Hero picks/bans analysis (present in raw `matches.json`, not extracted)
