# Dota 2 Pro Match Analysis — Claude Code Context

Public-facing Streamlit dashboard analysing Tier 1 Dota 2 pro match data pulled from the OpenDota API.

**Domain language lives in `CONTEXT.md`** — the glossary, the two data grains, the column dictionary, and the leagues covered. Read it before working on anything that touches metrics or naming. This file is rules and operational facts only.

## Agent skills

### Issue tracker

Specs and issues live as markdown files under `.scratch/<feature-slug>/`, each carrying a `status` and a separate `triage` value in frontmatter, with `.scratch/index.md` as the status board. Closed items move to `.scratch/_done/`. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context — one `CONTEXT.md` plus `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Current Status

- 1,822 matches across 14 Tier 1 tournaments (Oct 2025 – Aug 2026), patches 7.39 / 7.40 / 7.41 — and nine hotfixes, recorded but not yet surfaced
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

**The serving and modelling paths are split** (`10`, 2026-08-11): every match is fetched whole, a **Standard record** is extracted from it — 20.1 KB against a raw 288 KB — and the flat CSV is built from that rather than from a raw store. `data/matches_standard.jsonl` is committed, appended to and never rewritten; `SAVE_RAW` is a disabled seam writing `data/matches_raw.jsonl`. The CSV rebuilt byte-identical across all 1,822 rows. See ADR-0010.

**The legacy raw store is gone** (`11`, 2026-08-11): `data/matches.json` was reconciled against the Standard store and deleted. The reconciliation was stronger than the count the ticket asked for — all 1,822 raw payloads re-extracted to records identical to the ones already stored, and the CSV then rebuilt byte-identical from the store with the file absent. It is re-fetchable from OpenDota in about half an hour and no cold archive is kept; `data/matches.json` stays in `.gitignore` so re-enabling `SAVE_RAW` can never make a raw store committable by accident.

**The fetcher survives being left alone** (`12`, 2026-08-11): every call carries a 30-second timeout, a transient failure is retried on a bounded schedule that is not the rate limit's, and no single unreachable league or match can end a run. Each run keeps a record per league and prints one `RUN SUMMARY:` line at the very end, which is what `auto_update.py` logs — so a quiet run and a broken run are finally different entries. The CSV rebuilt byte-identical across all 1,822 rows. See ADR-0011.

**The back catalogue is audited and clean** (`14`, 2026-08-12): `audit_coverage.py` re-resolves a whole period rather than only the events nobody has mapped, and checks the other direction too. Over 2025-10-14 to 2026-08-12 — the dataset's own era, derived from its earliest match rather than written down — all **14** Tier 1 events resolve to the league already being fetched: no gaps, nothing awaiting a verdict, and no disagreement with the ledger, which needed no edit. **Seven** of the fourteen windows were contested and all seven resolved correctly; BLAST Slam IV's held **six** candidates, including the nested FISSURE PLAYGROUND 2, which ties it on team overlap at 100% and played *more* matches (124 against 96) — window coverage, 100% against 40.7%, is the only thing that parts them. `tests/test_audit.py::TestThe2025BackCatalogue` holds that against the recorded pool, because `TestThe2026Season` is a year with no nested windows in it at all. The one reverse-direction finding is The International 2026: active, and claimed by no event because it has not played a match yet.

**The patch is recorded at hotfix grain** (`16`, 2026-08-12): the CSV carries `patch_hotfix` beside `patch_label`, resolved from each match's `start_time` against Valve's own 117-entry patch list. Three buckets become nine — 7.39e (605), 7.40 (33), 7.40c (483), 7.41 (56), 7.41a (22), 7.41b (119), 7.41c (260), 7.41d (184), 7.41e (60) — and `patch_label` is untouched, so no figure on the dashboard moved. Nothing surfaces it yet; that is deliberate, and whatever does must show each bucket's match count. The boundary question the issue left open turned out to have a factual answer: **all 117 of Valve's timestamps are exactly midnight US/Pacific**, so the field is a release date dressed as a moment, and OpenDota's own patch id is reproducible from its constants' second-precision dates on 1,822 of 1,822 matches. `patch_label` therefore decides the gameplay patch and Valve's list only chooses the revision inside it — 28 matches of 2025-12-15 stay on 7.39e. See ADR-0012.

What is still missing is the last mile: a resolved league that nobody has approved is an `ATTENTION` line in the run's output and a row on that tab, not yet a pull request. That is `15`. The rest of `.scratch/tier1-pipeline-automation/` rebuilds correctness and hosting. Read ADR-0001 through 0004 before touching the pipeline, ADR-0009 as well before touching the resolver, and ADR-0010 before touching either store.

## File Structure

```
project/
├── opendota_pipeline.py      # pipeline shell — network, files, checkpoints, main()
├── liquipedia.py             # Liquipedia client shell — Tier 1 calendar, cache, rate limit
├── core.py                   # pure transform core — plain data in, plain data out
├── dashboard.py              # Streamlit dashboard
├── push_data.py              # bumps dashboard.py date + commits + pushes to trigger redeploy
├── auto_update.py            # scheduled daily run: pipeline, then push if new data
├── audit_coverage.py         # by hand, not scheduled: re-resolves a period in full, both directions
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
│       ├── audit_2025.json.gz # the 2025 candidate pool, recorded by the live audit
│       ├── valve_patch_list.json.gz  # Valve's 117 patch releases, envelope and all
│       └── hotfix_boundary.json.gz   # the 28 matches the two patch sources dispute
├── data/
│   ├── matches_standard.jsonl # the Standard store — committed, appended, 20 KB a match
│   ├── matches_raw.jsonl     # raw sink — LOCAL ONLY, off unless SAVE_RAW=true
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
python audit_coverage.py      # audit coverage against Liquipedia — by hand, minutes of API time
python -m pytest              # full suite, offline, about three seconds
```

## Rules

Non-negotiable when writing any new analysis or chart:

- **Never commit a raw payload store.** `data/matches_raw.jsonl` and the deleted `data/matches.json` are both gitignored and must stay that way — the sink is disabled, not removed, and it may be re-enabled for modelling, at which point the entry is the only thing standing between a 288 KB-a-match file and the repository. `data/matches_standard.jsonl` is the one that *is* committed.
- **The Standard store is appended to, never rewritten.** Adding a match must cost the size of that match. The shape this replaced rewrote the whole document every ten matches — 21 GB of disk writes to add 84 MB — and the cost was *total dataset × new matches*, so it degraded on every run. A whole-file rewrite of a store is the regression to watch for; use `append_text` for a log and `write_text_atomic` for a document. See ADR-0010.
- **Every field the flat row is built from is on `core.STANDARD_MATCH_FIELDS`.** The CSV is built from Standard records, so a field missing from that allowlist reads downstream as missing from the API. `tests/test_standard.py::TestTheServingPathIsUnaffected` flattens each recorded fixture from both the payload and its extract and compares — that test is what holds it, and adding a column to the flat row without adding its source field there is what breaks it.
- **Do not filter `first_blood_time_mins < 0`** — negative values are **valid pre-horn kills**, not artefacts. All 155 fall between −0.9 and −0.1 minutes. This reverses a previous rule; see ADR-0003.
- **Exclude `team_name.isin(["Radiant", "Dire"])`** from every team-level calculation. These are fallback names for anonymous teams, not real teams.
- **Display patch via `patch_label`, never `patch`.** `patch_label` is a string written by the pipeline; `patch` is the same value re-inferred by `read_csv` as a float, which renders `7.4` instead of `7.40`. Read the CSV with `core.CSV_DTYPES` so the label stays a string, and never reformat it — the label is already the name the API gave, and numeric formatting throws on `"Unknown"`. Order labels with `core.patch_sort_key`, not `float()`. `patch_hotfix` is the same kind of column and follows every one of these rules.
- **`patch_label` decides the gameplay patch; Valve's list only chooses the revision within it.** All 117 of Valve's `patch_timestamp` values are exactly midnight US/Pacific, so the field is a release *date* dressed as a moment — read literally it puts every match played from midnight onwards on release day onto a patch the client did not have yet, which cost 28 matches on 2025-12-15. Never resolve a hotfix that sits outside the gameplay patch the row already names, and never "correct" `patch_label` from Valve's list: `patch_hotfix` is derived inside it, which is what stops two columns of one file contradicting each other. See ADR-0012.
- **A blank `patch_hotfix` means "not resolved", never "no hotfix".** The CSV is rebuilt whole every run, so a failed fetch of Valve's list would blank a full column; `core.carry_forward_hotfix` fills from the CSV about to be replaced. It **fills and never corrects** — making it overwrite a resolved value would freeze the column at whatever the first run wrote and make a rule change unshippable.
- **`patch_hotfix` is a second column, not a replacement.** Three buckets become nine, four of them under 65 matches, and this dashboard's output is over/under percentages. Anything that ever surfaces a hotfix bucket shows its match count beside it.
- **Never drop matches to tidy a metric.** Flag them. Silently excluding rows — by `game_mode`, by data quality, by anything — is the class of behaviour that hid a whole tournament for two months. Suspect matches are excluded from *figures* and stay everywhere else: in the CSV, in the match count, and in match history, marked.
- **Compute every figure from `measured(df)`, never from the raw selection.** It drops the suspect rows, whose zeroes mean "unknown" rather than "nothing happened". A new chart, average or probability that reads `filtered` instead of `measured_matches` silently re-admits them. Say what was left out with `note_exclusions()`. See ADR-0007.
- **The Head to Head and Drilldown records count every match; the Tab 1–3 aggregate tables do not.** In the records — matches played, wins, win % — the figure is an outcome, which is right whether or not the replay parsed, and the full match list sits directly below it. In an aggregate table the match count is the denominator of the ten averages beside it, so the whole row is measured-only and the caption states what that cost. Both are deliberate; do not "make them consistent" without reading ADR-0007 first.
- **Distribution charts use match-level rows** (`measured_matches`), never team-perspective rows (`measured_teams`) — the latter double-counts every match.
- **Order tournaments by first match date, descending.** Computed once at startup as `league_start = raw.groupby("league_name")["start_time"].min()` and reused in the sidebar and the Drilldown tab.
- **Note or filter `non_captains_mode`** for anything draft-sensitive; the dataset is overwhelmingly mode 2 (Captain's Mode) and 12 matches are not. The flag is written at source on every row, so filter on it rather than re-deriving from `game_mode` — and say so in the chart when you do.
- **Keep `requirements.txt` on flexible ranges.** Exact pins broke imports on Streamlit Cloud, which defaults to Python 3.14. `requirements-dev.txt` follows the same rule.
- **New transformation logic goes in `core.py`, not the pipeline.** If it takes plain data and returns plain data, it belongs in the core and gets a test. Anything reaching the network, the filesystem, git or Streamlit stays in the shell. This is the seam the whole test suite hangs off — see `docs/adr/0005-pure-transform-core.md`.
- **Nothing in the test suite may touch the network.** Fixtures are recorded payloads under `tests/fixtures/`; the whole suite runs offline in about two seconds, and it stays that way. It was under one until the 2026 candidate pool arrived — 78 leagues and 29,000 matches, and worth the second. The page-sized fixtures are **session-scoped** in `conftest.py`; making them function-scoped again costs four seconds.
- **Every OpenDota call goes through `fetch_url`.** That is where the timeout, the retry policy and the rate-limit delay live, and a call made around it has none of them — which is exactly what `get_patch_map` was for two months. The same rule `liquipedia.py` follows for its own endpoint.
- **Nothing may read the pipeline's output except by its summary line.** `auto_update.py` calls `core.read_run_summary`; taking the last line of stdout is what made every log entry for two months read `Pipeline: first_blood_time_mins`. The prefix is a contract — the rest of the output stays a human's to read. See ADR-0011.
- **Liquipedia's four access conditions are code, not intent.** Any new call to `liquipedia.net` goes through `liquipedia.py` so it inherits the User-Agent, the 30-second `action=parse` interval and the cache. Anything displaying calendar data renders `core.LIQUIPEDIA_ATTRIBUTION`. These are the terms of the free API — see ADR-0006.
- **A Liquipedia page that parses to zero rows is a failed fetch, not an empty Tier 1 list.** Treating it as real would report every tournament missing at once, the first time a class name changes. `get_tier1_events` falls back and reports its `source`; never infer health from an empty list.
- **Coverage is `data/leagues.json`, and every league in it carries a verdict.** Never reintroduce a hardcoded league list, and never remove an entry to stop fetching a league — set its verdict to `rejected`, which is the record that it was considered. Deleting the entry makes it `pending` again on the next run, and a league that keeps reappearing as a candidate is a decision that was never taken.
- **Tournaments resolve by date window. Never compare the two sources' names, anywhere, for any purpose.** Liquipedia says `PGL Wallachia Season 7` where OpenDota says `PGL Wallachia 2026 Season 7`, and no normalisation fixes that in general. A candidate is a league whose matches *all* fall inside the event window — which is also the only reason qualifiers are excluded, so a rule that names them is a rule that has misunderstood the design. See ADR-0001.
- **Team overlap and window coverage rank candidates; neither ever gates them.** There is no minimum score for either. Qualifiers outscore the events they qualify for; the correct winner scored 85.8% on teams in one real window; and a tournament resolving on the day of its first match covers one day in twelve. Adding a threshold to either re-breaks what ADR-0001 was written to prevent, and would trade a wrong answer for no answer.
- **Tier 1 events nest inside one another, so a window can hold the right league and a smaller wrong one.** FISSURE PLAYGROUND 2 ran entirely inside BLAST Slam IV's 2025 window; both are 100% tracked teams, and the nested one played *more* matches. Coverage is what parts them. Never rank on match count before coverage.
- **The resolver never changes a verdict.** It writes `tier1_event` and nothing else. Recognising a tournament is the pipeline's job; deciding to cover it is Wade's, taken by merging a pull request (`15`). **The audit follows the same rule** — it re-derives a whole period and reports, and the one thing it may write is the same `tier1_event`. It prints the verdict edit rather than making it.
- **An audit reports in both directions or it is not an audit.** A coverage list can be wrong two ways and only one of them is visible from the calendar: a Tier 1 event with no league is a gap the daily run already surfaces, while a league being fetched that no Tier 1 event claims is invisible to it entirely. `audit_tracked_leagues` is that second direction, and it takes the **whole** calendar — handing it the audited slice reports every league from every other year as unlisted.
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
- `flatten_match(match, patch_map, releases=())` — one raw payload to one match row. **`patch_map` and `releases` are parameters, not globals** — building either is an API call, and injecting them is what makes this testable. Writes `patch_label` (always a string, `"Unknown"` when the patch is absent), `patch_hotfix`, `non_captains_mode`, `is_suspect` and `suspect_reason` alongside the raw values. `releases` defaults to empty, so a caller reading rows for something other than the patch — the audit reads them for team ids — need not fetch a patch list to get them.
- `patch_releases(patch_list)` / `resolve_hotfix(start_time, patch_label, releases)` / `gameplay_patch(name)` — Valve's list as an ascending release table, the hotfix a match was played on, and the patch a name belongs to. The whole boundary rule is `resolve_hotfix`: it takes the latest release at or before the match started **whose gameplay patch is the one `patch_label` names**, and falls back to `patch_label` itself when nothing has been lettered out yet. See ADR-0012.
- `carry_forward_hotfix(rows, previous)` — keeps a hotfix already in the CSV wherever this run resolved none, and returns `(rows, carried)`. Fills, never corrects.
- `suspect_reasons(match, objective_counts=None)` — every reason this match's objective data cannot be trusted, as a tuple; empty means the numbers stand. Missing objectives short-circuit, since the zero towers *are* the missing array rather than separate evidence. Pass the counts when you already have them; the verdict is the same either way.
- `retry_window_open(match, now)` — whether a suspect match is young enough to re-fetch. **The deadline hangs off the match's own `start_time`**, not off when the pipeline first saw it, which is what keeps it pure and what makes a backfilled event past its deadline on arrival.
- `classify_fetch(match, now)` — what to do with a match just fetched: `FETCH_COMPLETE`, `FETCH_HELD` or `FETCH_UNPARSED`. The whole re-fetch policy is this one function, and it is tested here rather than in the shell that acts on it.
- `retry_pause(status, attempt)` — seconds to wait before asking again, or None to stop. **`status` is None for a request that never got one** — a timeout, a dropped connection — which is transient by definition, since something below the application answered. The whole retry policy is this one function; the shell only asks, sleeps and gives up. See ADR-0011.
- `league_run_record(league_id, name)` / `matches_fetched(record)` / `summarise_run(records)` — a league's account of a run, how many matches it gave up, and the totals. The three fetch verdicts **are** the record's count keys, so the shell tallies `record[classify_fetch(...)]` rather than through a mapping that could drift. `fetched` is the three together: a held match cost a call and is in the store. `failed_leagues` carries names, not a count, because the count is a question and the name is the answer.
- `format_run_report(records)` / `format_run_summary(totals)` / `read_run_summary(output)` — the table a run prints, the one line it ends with, and the read of that line by `auto_update.py`. Write side and read side sit together because `RUN_SUMMARY_PREFIX` is a contract between two processes.
- `extract_standard(match)` — one raw payload to one Standard record, roughly 20 KB from 288 KB. **Idempotent**, so the same function serves the live pipeline and the one-off backfill. Presence-based: a field the payload does not carry is left out rather than nulled, since a record full of nulls would claim to know an unparsed match had no teamfights.
- `STANDARD_MATCH_FIELDS` / `STANDARD_PLAYER_FIELDS` / `STANDARD_TEAMFIGHT_FIELDS` — **allowlists**, so a field OpenDota adds tomorrow cannot enlarge a committed file on its own. A teamfight keeps `start`, `end`, `last_death`, `deaths` and not the per-player breakdown, which is a quarter of the record. See ADR-0010.
- `round_benchmarks(benchmarks)` — benchmark floats to `BENCHMARK_PRECISION`. Eighteen digits of a percentile is 8% of the store; rounded rather than dropped, because the benchmarks are an acceptance criterion.
- `format_json_lines(records)` / `parse_standard_records(lines)` — the store as text, and back. **Last write wins, in first-seen order**: a re-fetched match replaces its unparsed payload without moving its row in the CSV, which is what the in-memory upsert used to do. `parse_standard_records` returns `(records, damaged)` and never refuses a file over one bad line.
- `iter_json_array(chunks)` — top-level objects out of a JSON array arriving in chunks, one at a time. This is the only way the legacy 1 GB store can be read at all: `json.load` needs the document and its parsed form in memory together.
- `patch_sort_key(label)` — orders patch labels by release, tolerating lettered names and `"Unknown"`. Used by the dashboard's patch filter, and by `patch_hotfix` — the lettered branch was written as forward cover and needed no change when the column arrived.
- `CSV_DTYPES` — the dtypes `read_csv` needs so the CSV round-trips: `patch_label`, `patch_hotfix` and `suspect_reason` as `str`. Most hotfix names carry a letter and would survive anything; the first release of a gameplay patch does not, and `"7.40"` re-infers as `7.4` like any other. A blank `suspect_reason` still reads back as NaN — dtype cannot fix that, so `load_data()` fills it.
- **Objective timings convert on `is not None`, never on truthiness.** A first blood at exactly `t=0` is a real event, and a pre-horn one is negative; both are lost by an `if raw_time` test.
- `build_rows(matches, patch_map, releases=())` — one row per match, order preserved.
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
- `dataset_start_date(rows)` / `events_in_range(events, opens, closes)` — where an audit starts, and which events fall in it. The start is **derived from the data, never written down**: an event that finished before the dataset began is out of range rather than a coverage gap, and that line moves every time the back catalogue does. Both ends of the range are inclusive — the dataset's first match was played on its first day.
- `audit_findings(resolutions, ledger)` / `audit_tracked_leagues(ledger, events)` — the audit, run each way. A finding is `covered`, `untracked`, `declined` or `gap`, decided by the **verdict** rather than by whether a league was found, so it answers the question the audit was asked. `mismatch` is the one finding with no other home: the resolver never clears a mapping, so an answer from a year ago survives untested until something re-derives it. The reverse check takes the **whole** calendar, not the audited slice — handing it one year reports every league from every other year as unlisted.
- `recordable_resolutions(resolutions, findings)` — which of an audit's answers may be written back, and which are held. Returns both, so the caller says what it did not do rather than quietly doing less. A **mismatch** and a **league two events both claim** are withheld, because `apply_resolutions` keys by league id and either one would displace an answer rather than add one.
- `audit_totals(findings, tracked)` / `format_audit_report(...)` / `format_audit_next_steps(findings)` — the headline, the report and the edit each decision needs. A gap and a league awaiting a verdict are both "not in the dataset" and need opposite things done, so they are never pooled. An empty section prints `(none)` rather than vanishing, and **no events audited is reported as "nothing was checked", never as nothing missing** — with the reverse check still rendered underneath it, since that reads the whole calendar and depends on the period not at all.
- `resolution_problems(ledger, resolutions)` / `resolution_report(records, generated_at)` — the `ATTENTION` lines and the file. A resolved league nobody has judged is the whole feature in one line. Two things are deliberately silent: a **`rejected`** league, because "never proposed again" is what a verdict is for, and a **gap**, because an unstarted tournament printed as a fault every run is how a real one stops being read. A league claimed by two events *is* reported — `apply_resolutions` keys by league id, so the second claim would quietly evict the first.

## Data Pipeline (opendota_pipeline.py)

The shell around the core: network, files, checkpoints. Still organised in `# %%` cells:

1. Config, paths, `get_patch_map()` and `ensure_directories()` — both **called from `main()`, never at import**; **1b** the two file shapes — `write_text_atomic` / `append_text`; **1c** the Standard store — `read_standard_store` / `append_standard` / `append_raw` / `backfill_standard_store`
2. Rate-limited fetcher (`fetch_url`) — 1.1s delay, 60 calls/min free tier, an explicit timeout and `core.retry_pause` deciding what is asked again; **2b** the ledger — `read_ledger` / `write_ledger` / `fetch_all_leagues` / `load_ledger`; **2c** the resolver — `fetch_pro_matches` / `confirm_candidates` / `resolve_tier1_leagues` / `write_resolution`
3. **3a** checkpoint load/save; **3b** match detail fetcher — the **full** payload either way, since what to keep is `core.extract_standard`'s business
4. Main loop over the active leagues, match-level checkpoint only, appending a Standard record per match — and the raw payload too when the seam is open
5. `build_dataframe(patch_map, releases=())` reads the **Standard store**, calls `core.build_rows`, carries the hotfix column forward from the CSV it is about to replace, exports `matches_flat.csv`, then writes `data/meta.json` via `write_meta()`. Returns **`(df, rows)`** — the resolver needs the rows, where team ids are still ints rather than the floats-beside-NaN the frame re-reads them as

**`main()` fetches `/leagues` once and hands it to both callers.** `load_ledger(api_leagues)` takes it as a parameter, and `_FETCH_LEAGUES` is the sentinel that keeps "the caller passed nothing" apart from "the caller passed None because the fetch failed" — collapsing those would make a failed fetch trigger a second one, and a seeded ledger written over a real one.

**The resolver walks `/proMatches` backwards, then confirms each candidate in full.** The walk is cheap and gives windows *and* team ids in one pass, but a league that began before the walk did looks narrower than it is — and narrow is the direction that makes a league fit inside an event it has no business winning. So `confirm_candidates()` re-reads every shortlisted league's whole match list before it can win, and a league that cannot be re-read is dropped rather than judged on a partial window. A short walk therefore costs a missed candidate, recorded as a gap, never a wrong answer.

**Resolution runs last in `main()` and usually costs nothing.** `core.events_awaiting_resolution` skips events the ledger already maps, events that have not started, and events older than `core.RESOLVER_LOOKBACK_DAYS` (365) — so in steady state there is no walk at all. When a tournament opens, it resolves on the day of its first match.

**The lookback is load-bearing, not a tidy-up.** Liquipedia's calendar reaches back to 2005 and this project has never mapped most of it, so *every* one of those events is literally awaiting resolution. Without the horizon the first run walks twenty years of pro matches, and every run after it walks back to the oldest thing it still could not resolve. Auditing the back catalogue is `14` — done, and deliberately a separate command (`audit_coverage.py`) rather than a wider horizon here. `PRO_MATCHES_MAX_PAGES` (400) is the second stop, on the walk itself.

**`fetch_url` retries what `core.retry_pause` says to retry, and nothing else.** A 429 waits out the rate limit's minute (30/60/120s); a timeout, a dropped connection or a 5xx waits seconds (2/5/15); a 404 is not asked again at all. Every schedule ends after four attempts and the budget is **per call, not per kind** — the run has to fail a call and carry on, because a run that never finishes fetches nothing ever again. Nothing raises out of `fetch_url`: the retry path catches `requests.RequestException` and a second, non-retrying catch answers None to everything else, since one league's fetch raising is one league's fetch ending the run. The policy is a parameter, so a caller can be stricter and the tests can be instant. `DELAY_SECONDS` is 1.1, not 1.0: the free tier allows 60 calls a minute and the sleep starts *after* the response, so exactly one second sits on the limit rather than inside it. See ADR-0011.

**Every call carries `REQUEST_TIMEOUT_SECONDS`, and `get_patch_map` goes through `fetch_url` like everything else.** `requests` waits forever by default; on a workstation that is a hung terminal somebody notices, and on a six-hourly runner it is a job that never ends and a schedule that never runs again. The patch constants were the last call making their own request, with their own missing timeout and their own absent retry — nothing about them is a different kind of request.

**A run keeps a record per league and says what it did.** `run_pipeline` returns them, prints them as a table, and `main()` prints one `RUN SUMMARY:` line **after everything that could fail**, so its presence means the run reached the end and the summary **names** the leagues that failed. A league whose match-id list fails is recorded `list_failed` and the loop moves on; a match whose detail call fails is counted and deliberately **not** checkpointed, which is the same mechanism a held match uses. Reporting a failed league as zero matches is what makes a missing tournament look like a tournament that has not started.

**A match already in the checkpoint is skipped at the call site, not by reading `get_match_detail`'s `None`.** That `None` means *skipped or failed*, and a match id listed twice would otherwise be counted as a broken fetch — in the very line the run is judged by. Never restore a count that reads a verdict off an overloaded sentinel.

**An empty `/constants/patch` response is a failure**, like the league list and unlike a league's match list. There is no such thing as a Dota patch list with nothing in it, and a partial map labels real patches `Unknown`, which reads on the dashboard as a data problem rather than as the fetch that caused it — so dropped entries are reported too. **Valve's patch list follows the same rule**, plus one more: the datafeed reports success in a `success` field, so a 200 is not enough — the same trap the MediaWiki endpoint sets.

**`read_hotfix_column()` is the only place the pipeline reads its own output, and it is read to protect a column rather than to build one.** The CSV is rebuilt whole from the Standard store every run, which is what makes the store the single source of truth and also what would let one unreachable host blank `patch_hotfix` across 1,822 rows. Anything unreadable — no file, no column, a damaged CSV — is an empty mapping and never an exception: the column it guards is a nicety beside the dataset it is a column of.

**`write_resolution()` writes only when the answer changes.** The file is committed and pushed and appears in `auto_update.py`'s change check; rewriting it every run would put a commit in front of Wade every morning carrying a fresh timestamp and no news. `generated_at` is therefore when the answer was *reached*, not when it was last confirmed.

**`max_pages` on `fetch_pro_matches` resolves at call time, not as a default argument.** A module constant bound as a default binds once, when the function is defined, so a reassigned cap is honoured by nothing — the same rule the path parameters in `liquipedia.py` follow, and `tests/test_resolver_shell.py` is what holds it.

**`load_ledger()` is the whole coverage decision, and it has four cases.** No ledger: seed one from OpenDota's league list with nothing active, and fetch nothing this run — which leagues to cover is a judgement, and a pipeline that guessed would be the hardcoded dict again. A ledger that will not parse: fetch nothing and **write nothing**, because every verdict in that file survives a syntax error and none of it survives being written over. No league list: fetch what the ledger already says; discovery going quiet must never stop match fetching. Both: record anything new as `pending` and rewrite the file **only if that changed something**, because it is 10,000 lines and this runs daily. `tests/test_ledger_shell.py` holds all four.

**An empty `/leagues` response is a failure, which is the opposite of the rule the match loop follows.** A league's *match* list is legitimately `[]` before the event starts, so that loop tests `is None`. The *league* list is not — OpenDota has 10,050 — so the seed path tests `not api_leagues`. Seeding on an empty list writes a ledger holding nothing, and the next run greets every real league as newly discovered. `fetch_all_leagues()` likewise rejects a non-list payload: OpenDota can answer 200 with an error object, and a dict passed on to the ledger iterates as its own keys.

**`checkpoint_or_hold()` acts on `core.classify_fetch` and is the re-fetch mechanism.** A suspect match inside its five-day window is deliberately *not* added to `fetched_matches`, so the next run treats it as unfetched and fetches it again; once the window closes it is checkpointed, recorded in `checkpoints/unparsed_matches.json`, and never retried. Each run tallies complete / held / permanently unparsed and prints the three counts. Do not "fix" the missing `add()` — the absence is the feature, and `tests/test_suspect.py::TestClassifyFetch` is what holds it.

**A document is written atomically; a store is appended to.** `write_text_atomic()` writes to `<path>.tmp` and renames over the original, so an interrupt leaves the previous version rather than half of two — the ledger is 10,050 hand-made verdicts rewritten daily, and a truncated checkpoint means re-fetching the whole dataset. `append_text()` writes whole lines and fsyncs, so the cost of adding a match is the size of that match. **`write_text_atomic` passes no `newline=` argument on purpose**: every file it writes is already committed with the platform's line endings, and pinning them would turn the next run's diff into a whole-file rewrite. `append_text` pins `\n`, because that file is appended to by whichever machine ran the pipeline and will one day be two of them.

**`backfill_standard_store()` has already fired, and `data/matches.json` is gone (`11`).** It streamed the legacy 1 GB file into the Standard store when there was a raw file and no store, at the top of `main()`; both of its guards now hold permanently, so it is a no-op on every machine forever. It survives as the record of how the store was seeded and as the recovery path if that file is ever restored — it **deletes nothing**, and the delete it was waiting on was made by hand after reconciliation. Removing it is a tidy-up nobody has asked for; it costs one `os.path.exists` a run and six tests hold its behaviour.

**The module does nothing when imported.** `main()` is the only entry point and only the `__main__` guard calls it, so `python opendota_pipeline.py` and `auto_update.py` work exactly as before while `import opendota_pipeline` costs nothing. Reintroducing module-level execution breaks `tests/test_pipeline_shell.py`.

**To re-fetch a league's matches** (e.g. objectives were missing): remove those match IDs from `checkpoints/fetched_matches.json` and re-run. The store needs no editing — the new record is appended and wins over the old one on read.

**API:** OpenDota (`https://api.opendota.com/api`), 60 calls/min and 50k/month on free tier. Endpoints: `/leagues`, `/leagues/{id}/matches`, `/matches/{id}`, `/constants/patch`, `/proMatches`. One call a run goes elsewhere: `PATCH_LIST_URL` (`dota2.com/datafeed/patchnoteslist`), unauthenticated, the only source for hotfix names — and still through `fetch_url`, which is where the timeout and the retry schedule live. **`/explorer` is not used by anything scheduled** — it answers the whole candidate-pool question in one arbitrary-SQL call and was used by hand, once, to record `tests/fixtures/league_matches_2026.json.gz`. A daily job depending on it is one schema change from breaking.

## Coverage Audit (audit_coverage.py)

Run **by hand, never on a schedule**. The daily resolver looks forward — `events_awaiting_resolution` keeps only events that have started, are *not already mapped*, and opened inside the year — which is the right shape for a job that runs every morning and the wrong shape for the question this asks. The five 2025 leagues were chosen by hand before the resolver existed and nothing had ever re-derived them.

```bash
python audit_coverage.py                        # the dataset's whole span
python audit_coverage.py --from 2025-10-01 --to 2025-12-31
python audit_coverage.py --dry-run              # report, write nothing
```

- **The period defaults to the data, not to a constant.** The start is `core.dataset_start_date` — the day of the dataset's earliest match — because an event that finished before the dataset began is out of range rather than a gap. The end is today, because an unstarted event has nothing to find and the daily run already reports it as an upcoming gap.
- **It writes `tier1_event` and nothing else.** `apply_resolutions` never touches a verdict, so the worst an audit can do to the ledger is name the tournament a league played. Covering one is Wade's edit, and the run prints exactly which line to change. An audit that activated a league would be the automatic import the ticket rules out.
- **A mismatch is never written back**, and neither is a league two events both claim. `apply_resolutions` keys its writes by **league id**, so re-pointing an event at a second league does not *move* the mapping — it leaves the first league still naming the event and overwrites whatever the second one used to name. One write, two wrong answers, and a third event silently a gap. `core.recordable_resolutions` holds both cases back and the run says which; `confirm_candidates` drops any league it cannot re-read, so one timed-out request is all it takes to promote a runner-up and reach this.
- **`--dry-run` means it, which is why it does not use `pipeline.load_ledger`.** That function records newly discovered leagues and rewrites the 10,050-line file when it finds any, and seeds a fresh one when the file is missing — right for a daily run, and not something a command promising to write nothing may do. A dry run gets `read_ledger` and reports rather than repairs a file it cannot parse.
- **It refuses rather than guesses.** No tracked teams means the tiebreaker is measured against nothing, every candidate scores zero and the ranking falls through to coverage and match count — so the run stops instead of writing that answer. An **empty** `/leagues` response stops it too, by the same rule the ledger's seed path follows: an empty league list leaves every summary with no tier, which silently switches off the prune keeping showmatch series out of a Tier 1 window.
- **`--from` and `--to` are validated where they are typed.** The comparison downstream is a *string* comparison against Liquipedia's dates, so `2025-1-1` sorts below every real one and silently widens the range to the whole calendar; a backwards range selects nothing and reads as "nothing was checked", which looks like an answer. Both are `argparse` errors.
- **`data/tier1_resolution.json` is deliberately left alone.** That file's horizon is this year and next; an audit of the back catalogue writing history into it would put events nobody can act on onto the dashboard's Upcoming tab.
- **The walk cap is the audit's own** (`AUDIT_MAX_PAGES`, 1000 pages), because this walk reaches back to the start of the dataset rather than to the oldest unresolved event of the last day or two. It costs nothing unless it is needed — the walk stops the moment it crosses its cutoff either way.
- **The candidate pool is confirmed, not walked.** The shortlist comes from `/proMatches` and every league on it is re-read in full through `pipeline.confirm_candidates` before it can win a window, for the same reason the daily run does it: a league that began before the walk did looks narrower than it is, and narrow is the direction that makes a league fit inside an event it has no business winning.
- **What a full audit costs**, measured on 2026-08-12: 20,600 pro matches over 206 pages to reach 2025-10-11, 27 shortlisted leagues re-read, about 235 calls and seven minutes including two rate-limit backoffs. That is why it is not scheduled — and why `13`'s six-hourly cadence must never grow an audit step.
- The teams the tiebreaker is measured against come from the Standard store through the same flattening the CSV uses, so the audit ranks against exactly the set the daily run does. The patch map passed to `build_rows` is `{}` on purpose — these rows are read for their team ids, and fetching the patch constants for a column nobody reads is a call spent on nothing.

## Liquipedia Client (liquipedia.py)

Obtains the Tier 1 calendar that defines the dataset's scope. Parsing lives in `core.py`; this is the network, the cache and the clock. **Importing it does nothing** — every entry point takes its collaborators as arguments, which is how the tests run offline.

**API:** `https://liquipedia.net/dota2/api.php`, `action=parse&prop=text` on `Tier_1_Tournaments`. This is the free MediaWiki endpoint and needs **no API key** — not `api.liquipedia.net`, which is the paid v3 product. See ADR-0006.

- `ParseRateLimiter` — one `action=parse` per 30s. Clock and sleep injected. **Nothing in the project reaches it.** It was put there for issue 14, which was expected to walk several years of the page in one run and turned out to need a single call like everything else — the Tier 1 page is one table covering 2005 to 2027, so a period is a slice of one response. It stays because it is a term of the free API, not because a caller needs restraining, and `test_liquipedia_client.py` is the only thing holding it. **`fetch_tier1_html` defaults to the process-wide `_PARSE_LIMITER`** — building one per call gives each an empty history and enforces nothing, which is how the interval once held in the tests and nowhere else.
- `fetch_tier1_html(page, limiter, get)` — returns HTML or `None`. **Never raises.** A MediaWiki error arrives as HTTP 200 with an `error` key, so the status code alone does not mean success.
- `get_tier1_events(...)` — returns `{"events": [...], "source": ...}`. Degrades fresh cache → network → stale cache → committed seed calendar. **Read the `source`**; an empty list is not a health signal.
- `data/tier1_calendar.json` — the committed fallback, generated from the same table rather than typed. It stayed its own file rather than being absorbed into the ledger as the spec expected: the calendar is Liquipedia's list of *events*, keyed by date window, and `data/leagues.json` is OpenDota's list of *leagues* with this project's verdict on each. Joining them is the resolver, `tier1-pipeline-automation/08`. See ADR-0008.
- The cache is 24h, in `checkpoints/` and local-only.

## Hosting

- Streamlit Community Cloud, redeploys automatically on every push to `master`
- Six data files are committed: `matches_flat.csv`, `meta.json`, `tier1_calendar.json`, `leagues.json`, `tier1_resolution.json` and `matches_standard.jsonl`. The raw sink and the Liquipedia cache stay local. All six are in the `DEPLOYED` list in `push_data.py` **and** `auto_update.py` — adding a new deployed data file means editing both. Nothing deployed reads the Standard store; it ships because a modelling asset living on one workstation is the thing the artifact split was for.
- `auto_update.py` logs the run's own `RUN SUMMARY:` line via `core.read_run_summary`, and logs `FINISHED WITHOUT A SUMMARY` when there is none — a pipeline that exited 0 without reaching the end of `main()`. A non-zero exit is still `FAILED` with stderr, as before.
- `auto_update.py` exits early when nothing changed, and **the ledger and the Tier 1 resolution both count as change alongside the CSV**. A run that fetched no matches but discovered a new league has found the one thing this job exists to notice; testing the CSV alone would leave that `pending` entry dirty in the working tree until somebody happened to look. The resolution file is safe to test on because it is only rewritten when the mapping itself moves.
- Streamlit may not redeploy on CSV-only pushes — always use `push_data.py`, which bumps a `# data: YYYY-MM-DD` comment in `dashboard.py` so a `.py` file always changes. The `ttl=1800` on `load_data()` is the safety net.

## Key Technical Decisions

- `SAVE_RAW` — whether the full raw payload is *also* written to `data/matches_raw.jsonl`. **Defaults off**, everywhere: 288 KB a match can live neither in the repository nor on a runner. Every match is fetched in full regardless, and a Standard record is extracted from it, so this is a seam and not a source. Set `SAVE_RAW=true` to re-open it for modelling — a configuration change, which is the acceptance criterion ADR-0002 wrote it for.
- The Standard record is 20.1 KB a match against a raw 288 KB, measured, and the store 37.5 MB for 1,822 matches — against the 20 KB and 35 MB ADR-0002 estimated. Two things paid for that, both recorded in ADR-0010: the per-fight player breakdown, and four decimal places of benchmark precision. Keeping every non-timeseries player field came to 38 KB a match, which passes GitHub's 100 MB file limit inside two seasons — the 20 KB in ADR-0002 was not free.
- Match-level checkpoint only — league match-ID lists are always re-fetched so new matches are detected.
- Buildings counted via `goodguys`/`badguys` in the `key` field, not the `team` field, which is absent on `building_kill` events.
- Patch IDs mapped dynamically from the OpenDota constants API at the start of `main()`, then passed down as an argument. The mapped name is written twice — as `patch`, which `read_csv` re-infers as a float, and as `patch_label`, the string the dashboard reads. `patch` has no consumer left in this repo; it stays because removing a column from a published CSV is a bigger change than issue `04` asked for, and it is a candidate for deletion, not for use.
- The hotfix release table is fetched beside the patch map, from a **different vendor**, and the two are not peers: OpenDota's constants name the gameplay patch and Valve's list names the revision within it. Two sources, two grains, one of them subordinate — which is the shape the resolver already uses for Liquipedia and OpenDota, and for the same reason. ADR-0012 records why reading Valve's timestamps literally is wrong.
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
- **Reproducing 2026 exactly is not proof the resolver is right.** No two Tier 1 events overlap in 2026, so the whole acceptance criterion is silent on nested windows — the case that mismapped BLAST Slam IV on the first live run. The 2025 nesting is now a test of its own, against the pool the live audit recorded. Assume the same of the next criterion: a year that happens not to contain a case does not rule it out.
- **The confirmation pass is load-bearing and now has real data behind it.** Three of the fifteen leagues in the 2025 fixture — the Ancients League, the European Pro League and the Snake Trophy — were shortlisted because the walk had only seen the part of them that fell inside a Tier 1 window; the European Pro League actually runs from November 2024. Each spans months once re-read in full and falls out before anything is scored. This is why nothing may win on the walk's own summary.
- **"Qualifiers fall outside main event windows" is true in most cases and not in general.** 2026 had one counter-example (Road To EWC's regional qualifiers inside BLAST SLAM VII); 2025 has a second, BLAST Slam V's China Closed Qualifier sitting inside BLAST Slam *IV*'s window. Both lose on team overlap and neither is excluded by a date. A rule that names qualifiers is a rule that has misunderstood the design — but so is a claim that the date always handles them.
- The worst case for API spend is a started event that never resolves: it stays awaiting for a year and every run walks the full 259 pages looking for it. Affordable daily, not comfortable at the six-hourly cadence `13` introduces — the fix is a shorter horizon on how long a *gap* keeps being retried, and it belongs with that change.
- The ledger was seeded with the *curated* league names from the retired `ALL_LEAGUES` dict, not OpenDota's. `league_name` is written onto every match row and grouped on by the dashboard, so seeding "SLAM IV" over "Slam IV" would have split that tournament in two the next time a match was fetched.
- Replacing the dict with the ledger was verified by running the full pipeline and confirming `matches_flat.csv` came out byte-identical across all 1,822 rows.
- Splitting the serving and modelling paths was verified the same way: all 1,822 matches extracted to Standard, the flat rows rebuilt from the store, `matches_flat.csv` byte-identical. Three refactors, one ritual — if a change to the pipeline cannot be checked this way, that is worth noticing before it lands.
- The Standard store is read **last-write-wins in first-seen order**, which is where the "a match fetched twice must not be counted twice" invariant now lives. It used to be an in-memory upsert (`store_match`), and moving it to the read side is what let the write side become an append.

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
- Player-level stats (retained in `data/matches_standard.jsonl`, not surfaced)
- Hero picks/bans analysis (retained in `data/matches_standard.jsonl`, not surfaced)
