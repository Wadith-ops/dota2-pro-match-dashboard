# Dota 2 Pro Match Analysis — Claude Code Context

## Project Overview
Public-facing Streamlit dashboard analysing Tier 1 Dota 2 pro match data pulled from the OpenDota API.
Pivoted from EDA to a shareable dashboard. Pipeline automation for live tournaments is planned but not yet implemented.

## Current Status
- Data pipeline complete and working
- 1,279 matches collected across 10 Tier 1 leagues (2024–2026), patches 7.39 / 7.40 / 7.41
- Dashboard live at https://dota2-pro-match-dashboard-9kymmqtgrymab25ofas4oh.streamlit.app/
- GitHub repo: https://github.com/Wadith-ops/dota2-pro-match-dashboard
- `matches.json` (705 MB) is local-only and gitignored; `matches_flat.csv` (220 KB) is the deployment data source

## File Structure
```
project/
├── opendota_pipeline.py      # main data pipeline (Steps 1-5)
├── dashboard.py              # Streamlit dashboard
├── requirements.txt          # pinned dependencies for deployment
├── .gitignore                # excludes matches.json, checkpoints/, __pycache__/
├── CLAUDE.md                 # this file
├── data/
│   ├── matches.json          # raw match data — LOCAL ONLY, do not commit
│   └── matches_flat.csv      # flattened data — committed to GitHub for deployment
└── checkpoints/
    └── fetched_matches.json  # pipeline checkpoint — local only
```

## Dashboard (dashboard.py)
Streamlit + Plotly. 3-tab layout with global sidebar filters.

**Sidebar filters:** League (multi-select), Team (multi-select, defaults empty = all teams), Patch (multi-select), Side (Radiant/Dire/Both — single team only)

**Tabs:**
- **Tab 1 — Team**: KPI row (9 metrics), then Roshan / Kills / Barracks each as grouped bars by patch and by tournament, plus game length histogram and box plot by patch
- **Tab 2 — Tournament**: stats table (all 4 metrics per tournament) + 4 comparison bar charts
- **Tab 3 — Meta Trends**: per-patch KPI columns + 4 comparison charts (roshans, kills, barracks, game length violin)

**Four core metrics across all tabs:** Roshan, Kills (hero kills/score), Barracks, Game Length

**Key helpers:**
- `load_data()` — reads CSV, parses `start_time`, adds `total_roshan`, `total_kills`, `total_barracks`, `both_lost_barracks`, `patch_label`
- `build_team_perspective(df_hash, df)` — pivots match rows into one row per team per match; adds `team_won`, `side`, `got_first_roshan`, `team_kills`, `team_barracks_killed`, plus match-level columns `total_roshan`, `total_kills`, `total_barracks`, `both_lost_barracks`
- `@st.cache_data` used on both; `df_hash=str(len(df))` is the cache key workaround for DataFrames
- `ANON_NAMES = {"Radiant", "Dire"}` — filtered from all team-perspective calculations

**Key derived columns:**
- `total_kills = radiant_score + dire_score`
- `total_barracks = radiant_barracks_lost + dire_barracks_lost`
- `both_lost_barracks = (radiant_barracks_lost >= 1) & (dire_barracks_lost >= 1)`
- `team_barracks_killed` — barracks destroyed by the team (= opponent's barracks_lost)

**Data quirks to handle in all new charts:**
- `first_blood_time_mins < 0` — pre-game artefacts, filter before any chart using this column
- `team_name.isin(["Radiant", "Dire"])` — default fallback names for anonymous teams, exclude from team-level analysis
- `patch` column is a float (7.39, 7.4, 7.41) — always use `patch_label` for display (formats 7.4 → "7.40")
- `game_mode` — 1,268 / 1,279 matches are mode 2 (Captain's Mode); note or filter for mode-sensitive stats
- For distribution charts (histograms), use `filtered` (match-level, one row per match) not `team_filtered` to avoid duplicate values per match

## Hosting
- Live URL: https://dota2-pro-match-dashboard-9kymmqtgrymab25ofas4oh.streamlit.app/
- GitHub: https://github.com/Wadith-ops/dota2-pro-match-dashboard
- Platform: Streamlit Community Cloud — redeploys automatically on every push to `master`
- Data: only `matches_flat.csv` committed to repo; `matches.json` stays local
- To update live data: run pipeline locally → regenerate CSV → commit `data/matches_flat.csv` → push
- Note: use flexible version ranges in `requirements.txt` (not exact pins) — Streamlit Cloud runs Python 3.14 by default and exact pins caused import failures

## Data Pipeline (opendota_pipeline.py)
Split into 5 steps using `# %%` cells in VS Code:
- **Step 1** — Config, league definitions, patch map fetched from OpenDota constants API
- **Step 2** — Rate limited API fetcher (`fetch_url`), 1 second delay, 60 calls/min free tier
- **Step 3a** — Checkpoint loading/saving + league match ID fetcher
- **Step 3b** — Match detail fetcher with `SAVE_RAW = True`
- **Step 4** — Main loop, match-level checkpoint only, appends to matches.json
- **Step 5** — Flattens raw JSON to pandas DataFrame, exports matches_flat.csv

**Pipeline fixes done:** `os.chdir()` removed (replaced with `Path(__file__).parent` anchoring); `SAVE_RAW` now reads from env var (defaults `true` locally, set `false` in CI).

## Updating Live Data (Manual Process)
Run locally after tournaments, then push the updated CSV:
1. Run `opendota_pipeline.py` — fetches only new matches (checkpoint skips already-fetched IDs)
2. Run Step 5 to regenerate `matches_flat.csv`
3. `git add data/matches_flat.csv` → commit → push → Streamlit Cloud redeploys automatically

## Data Sources
- API: OpenDota (https://api.opendota.com/api)
- Rate limit: 60 calls/minute, 50k calls/month (free tier)
- Endpoints used:
  - GET /leagues/{league_id}/matches
  - GET /matches/{match_id}
  - GET /constants/patch

## Leagues Covered
| ID    | Name                        |
|-------|-----------------------------|
| 17419 | Slam IV                     |
| 18863 | FISSURE PLAYGROUND 2        |
| 18920 | PGL Wallachia 2025 Season 6 |
| 17420 | Slam V                      |
| 18988 | DreamLeague Season 27       |
| 19099 | BLAST Slam VI               |
| 19269 | DreamLeague Season 28       |
| 19435 | PGL Wallachia 2026 Season 7 |
| 19422 | ESL One Birmingham 2026     |
| 19543 | PGL Wallachia 2026 Season 8 |

## Dataset Columns (matches_flat.csv)
**Match info:** match_id, league_id, league_name, patch, start_time, duration_secs, duration_mins, radiant_win, radiant_score, dire_score, game_mode

**Teams:** radiant_team_id, radiant_team_name, dire_team_id, dire_team_name

**Objectives:**
- Roshan: radiant_roshan_kills, dire_roshan_kills, first_roshan_time, first_roshan_time_mins, first_roshan_team
- Aegis: aegis_stolen, aegis_denied
- Tormentors: radiant_tormentor_kills, dire_tormentor_kills
- Buildings: radiant_towers_lost, dire_towers_lost, radiant_barracks_lost, dire_barracks_lost
- Other: first_blood_time, first_blood_time_mins, courier_kills

## Key Technical Decisions
- `SAVE_RAW = True` — full raw API response saved in matches.json (enables future hero/player extraction)
- Match-level checkpoint only — pipeline always re-fetches league match ID lists to detect new matches
- Buildings counted via `goodguys`/`badguys` in `key` field (not `team` field, which is absent on building_kill events)
- Patch IDs mapped dynamically from OpenDota constants API at startup
- Columns named `radiant_towers_lost` / `dire_towers_lost` — these are buildings destroyed, not captured

## Adding New Leagues
1. Add to `ALL_LEAGUES` dictionary in Step 1
2. `ACTIVE_LEAGUES = list(ALL_LEAGUES.keys())` handles the rest automatically
3. Run pipeline — only new matches will be fetched
4. Run Step 5 to regenerate `matches_flat.csv`
5. Commit updated CSV and push to GitHub (Streamlit Cloud redeploys automatically)

## Environment
- Python 3.11.5
- Key libraries: requests, pandas, plotly, streamlit

## Next Steps
1. ✅ Create `.gitignore` and `requirements.txt`
2. ✅ Set up GitHub repo and deploy to Streamlit Community Cloud
3. ✅ Expand `dashboard.py` to 3-tab layout (Team / Tournament / Meta Trends) — shipped 2026-05-02
4. Pipeline run manually — `run_update.py` and Windows scheduled task removed (automation didn't work reliably)

## Out of Scope for Now
- Match outcome or duration prediction modelling
- Player-level stats (available in raw matches.json but not yet extracted)
- Hero picks/bans analysis (available in raw matches.json but not yet extracted)
