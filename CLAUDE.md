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

The underlying cause is not fixed. League coverage is still a hardcoded dict that cannot discover a tournament — the rest of `.scratch/tier1-pipeline-automation/` rebuilds coverage, correctness and hosting. Read ADR-0001 through 0004 before touching the pipeline.

## File Structure

```
project/
├── opendota_pipeline.py      # main data pipeline (Steps 1-5)
├── dashboard.py              # Streamlit dashboard
├── push_data.py              # bumps dashboard.py date + commits + pushes to trigger redeploy
├── auto_update.py            # scheduled daily run: pipeline, then push if new data
├── requirements.txt          # flexible version ranges — NOT exact pins
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
├── data/
│   ├── matches.json          # raw match data — LOCAL ONLY, do not commit
│   ├── matches_flat.csv      # flattened data — committed, this is what deploys
│   └── meta.json             # coverage record — committed, read by the dashboard
└── checkpoints/
    └── fetched_matches.json  # pipeline checkpoint — local only
```

## Commands

```bash
python opendota_pipeline.py   # fetch new matches (checkpoint skips already-fetched IDs)
python push_data.py           # commit + push CSV and dashboard, triggers redeploy
python auto_update.py         # both of the above, for the daily schedule
```

## Rules

Non-negotiable when writing any new analysis or chart:

- **Never commit `data/matches.json`.** It is gitignored and must stay that way — including after the raw sink is disabled, since it may be re-enabled for modelling.
- **Do not filter `first_blood_time_mins < 0`** — negative values are **valid pre-horn kills**, not artefacts. All 155 fall between −0.9 and −0.1 minutes. This reverses a previous rule; see ADR-0003.
- **Exclude `team_name.isin(["Radiant", "Dire"])`** from every team-level calculation. These are fallback names for anonymous teams, not real teams.
- **Display patch via `patch_label`, never `patch`.** The raw column is a float and renders `7.4` instead of `7.40`. Being fixed at source in `tier1-pipeline-automation/04`; until then the rule stands.
- **Never drop matches to tidy a metric.** Flag them. Silently excluding rows — by `game_mode`, by data quality, by anything — is the class of behaviour that hid a whole tournament for two months. Suspect matches are to be excluded from *averages* but stay in match history — **not built yet**, it lands in `tier1-pipeline-automation/05`. Until then the five suspect matches sit in every average; do not assume they are filtered.
- **Distribution charts use match-level rows** (`filtered`), never team-perspective rows (`team_filtered`) — the latter double-counts every match.
- **Order tournaments by first match date, descending.** Computed once at startup as `league_start = raw.groupby("league_name")["start_time"].min()` and reused in the sidebar and the Drilldown tab.
- **Note or filter `game_mode`** for anything draft-sensitive; the dataset is overwhelmingly mode 2 (Captain's Mode).
- **Keep `requirements.txt` on flexible ranges.** Exact pins broke imports on Streamlit Cloud, which defaults to Python 3.14.
- **Closing an issue is three moves, not one:** set `status: done`/`dropped`, move the file to `.scratch/_done/` mirroring its path, and move its row to Closed in `.scratch/index.md` — all in the same turn. See `docs/agents/issue-tracker.md`.

## Dashboard (dashboard.py)

Streamlit + Plotly. 5-tab layout with global sidebar filters.

**Sidebar filters:** League (multi), Team (multi, empty = all), Patch (multi), Side (Radiant/Dire/Both — single team only)

**Coverage line:** a caption between the title and the tabs, stating data date, latest match date, match count, tournament count and excluded count. It renders *before* the empty-filter guard, so it still shows when the filters select nothing.

**Tabs:**
- **1 — Team**: 9-metric KPI row, then Roshan / Kills / Barracks each as grouped bars by patch and by tournament, plus game length histogram and box plot by patch
- **2 — Tournament**: stats table (all 4 metrics per tournament) + 4 comparison bar charts
- **3 — Meta Trends**: per-patch KPI columns + 4 comparison charts (roshans, kills, barracks, game length violin)
- **4 — Head to Head**: two teams; record (matches, wins, win %), 4 avg stat bar charts (A / B / match total), match history, Both Lost Racks % + Both Slew Rosh %, over/under calculator
- **5 — Drilldown**: independent tournament + team filters (at least one required); record/win % if a team is selected, 4 avg stat bar charts, match history, same probability stats and over/under calculator as H2H

**Key helpers:**
- `load_data()` — reads CSV, parses `start_time`, adds the derived columns listed in `CONTEXT.md`
- `build_team_perspective(df_hash, df)` — pivots match rows into one row per team per match
- `load_meta()` — reads `data/meta.json`, returning `{}` when absent or malformed
- `coverage_line(df, meta)` — builds the coverage caption. **Counts come from the CSV, never from `meta.json`**, so the line cannot advertise coverage the loaded page does not have; only `generated_at` and `excluded_count` are read from meta
- `format_date()` — renders `5 Aug 2026`. Does not use `%-d`, which is not portable to Windows
- `load_data()` is `@st.cache_data(ttl=1800)`; `build_team_perspective()` is `@st.cache_data` with no ttl
- `df_hash=str(len(df))` is **redundant** — Streamlit hashes DataFrame arguments unless the parameter name starts with `_`, so the cache key already covers contents. Do not copy this pattern into new cached functions.
- `ANON_NAMES = {"Radiant", "Dire"}`

## Data Pipeline (opendota_pipeline.py)

Split into 5 steps using `# %%` cells:

1. Config, league definitions, patch map from the OpenDota constants API
2. Rate-limited fetcher (`fetch_url`), 1s delay, 60 calls/min free tier
3. **3a** checkpoint load/save + league match ID fetcher; **3b** match detail fetcher
4. Main loop, match-level checkpoint only, appends to `matches.json`
5. Flattens raw JSON to a DataFrame, exports `matches_flat.csv`, then writes `data/meta.json` via `write_meta()`

**To re-fetch a league's matches** (e.g. objectives were missing): remove those match IDs from `checkpoints/fetched_matches.json` *and* those records from `data/matches.json`, then re-run.

**API:** OpenDota (`https://api.opendota.com/api`), 60 calls/min and 50k/month on free tier. Endpoints: `/leagues/{id}/matches`, `/matches/{id}`, `/constants/patch`.

## Hosting

- Streamlit Community Cloud, redeploys automatically on every push to `master`
- Only `matches_flat.csv` and `meta.json` are committed; `matches.json` stays local. Both are in the `git add` list in `push_data.py` **and** `auto_update.py` — adding a new deployed data file means editing both.
- Streamlit may not redeploy on CSV-only pushes — always use `push_data.py`, which bumps a `# data: YYYY-MM-DD` comment in `dashboard.py` so a `.py` file always changes. The `ttl=1800` on `load_data()` is the safety net.

## Key Technical Decisions

- `SAVE_RAW` — full raw API response saved to `matches.json`, enabling future hero/player extraction. Reads from an env var; defaults `true` locally, set `false` in CI.
- Match-level checkpoint only — league match-ID lists are always re-fetched so new matches are detected.
- Buildings counted via `goodguys`/`badguys` in the `key` field, not the `team` field, which is absent on `building_kill` events.
- Patch IDs mapped dynamically from the OpenDota constants API at startup.
- `radiant_towers_lost` / `dire_towers_lost` name buildings *destroyed*, framed as losses by the owner.
- `os.chdir()` removed from the pipeline in favour of `Path(__file__).parent` anchoring.
- **An empty league match list is not a failure.** `fetch_url()` returns `None` on failure and a list on success, so the main loop tests `if data is None`, never `if not data`. A league added before it starts — The International 2026 — legitimately returns `[]`, and truthiness would log it as a failed fetch.

## Adding New Leagues

A league belongs in the dataset if it is on [Liquipedia's Tier 1 Tournaments list](https://liquipedia.net/dota2/Tier_1_Tournaments) — not if OpenDota calls it `professional`, which 2,468 leagues are. Qualifiers, Division 2 and regional events are out. See ADR-0001.

Current process, until the ledger lands (`tier1-pipeline-automation/06`):

1. Add to the `ALL_LEAGUES` dict in Step 1
2. `ACTIVE_LEAGUES = list(ALL_LEAGUES.keys())` handles the rest
3. Run the pipeline — only new matches are fetched
4. Run `python push_data.py`
5. Add the league to the table in `CONTEXT.md`

To find the OpenDota league id for a Tier 1 event, **match on dates, never on names** — the two sources name tournaments differently. Take the event's date window from Liquipedia and find the league whose matches fall inside it; if more than one does, the right one is the league with the most teams already in the dataset.

## Environment

Python 3.11.5. Key libraries: requests, pandas, plotly, streamlit.

## Out of Scope for Now

- Match outcome or duration prediction modelling
- Player-level stats (present in raw `matches.json`, not extracted)
- Hero picks/bans analysis (present in raw `matches.json`, not extracted)
