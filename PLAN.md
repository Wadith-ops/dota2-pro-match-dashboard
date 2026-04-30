# Project Plan: Pivot from EDA to Public Dashboard

## Context
Originally an EDA / data science project. Pivoting to a **public-facing Streamlit dashboard** that others can view, with pipeline automation planned for future tournaments.

---

## Phase 1: Repo + Hosting Setup
**Status: ✅ Complete**

1. ✅ Created `.gitignore` — `matches.json`, `checkpoints/`, `__pycache__/` excluded
2. ✅ Created `requirements.txt` — flexible version ranges (`>=`) to ensure Streamlit Cloud compatibility
3. ✅ `git init`, committed only the right files — `matches.json` never tracked
4. ✅ Pushed to https://github.com/Wadith-ops/dota2-pro-match-dashboard
5. ✅ Live at https://dota2-pro-match-dashboard-9kymmqtgrymab25ofas4oh.streamlit.app/

Note: exact version pins caused a `ModuleNotFoundError` on Streamlit Cloud (Python 3.14 environment); switched to `>=` ranges to let the cloud resolver pick compatible versions.

---

## Phase 2: Dashboard Expansion
**Status: Needs review — edit this section before implementing**

The sections below are a first draft. Review and amend before starting a new session to implement. Change, remove, or add anything that doesn't fit what you want.

Expand `dashboard.py` from single-page Roshan analysis to a 4-tab layout.

### Layout change
- Page title: `"Dota 2 Pro Match Analysis"`
- Wrap all content in `st.tabs()`

### Tab 1 — Roshan & Objectives *(existing — no changes)*

### Tab 2 — Team Performance
- Win rate leaderboard (≥10 games, `RdYlGn` scale, 50% reference line)
- Radiant vs. Dire split per team (≥10 games per side)
- Head-to-head stat block + pie chart (only shown when exactly 2 teams selected)
- Objective efficiency: avg Roshan / tormentor / courier kills per team

### Tab 3 — Meta Trends
- KPI row per active patch selection (avg duration, Roshan/game, tormentors/game, radiant WR%)
- Patch comparison grouped bar chart (7.39 / 7.40 / 7.41)
- Duration violin plot grouped by patch
- Objective trend line chart (patches on x-axis, Roshan / tormentors / couriers as lines)
- First blood timing box plot by patch (filter `first_blood_time_mins > 0`)

### Tab 4 — League Overview
- League stats table (N matches, date range, avg duration, radiant WR, avg Roshan/game)
- Tournament timeline Gantt chart (`px.timeline`)
- League comparison chart with metric selector
- Match distribution donut chart

### Data gotchas to handle
- `first_blood_time_mins < 0` → filter before any chart using this column
- `team_name.isin(["Radiant", "Dire"])` → exclude from team-level analysis (fallback names for unknown teams)
- `patch` is a float → always use `patch_label` column for display (formats 7.4 → "7.40")

---

## Phase 3: Pipeline Automation
**Status: Deferred — manual update process chosen for now**

### Chosen approach: Option A (manual local runs)
Run the pipeline locally after tournaments, then commit and push the updated CSV:
1. Run `opendota_pipeline.py` locally — fetches only new matches (checkpoint skips already-fetched IDs), appends to `matches.json`
2. Run Step 5 (`build_dataframe`) — regenerates `matches_flat.csv`
3. `git add data/matches_flat.csv` → commit → push → Streamlit Cloud redeploys automatically

This works because `matches.json` and the checkpoint live locally and persist between runs.

### Why full automation is deferred
GitHub Actions runners start with no `matches.json` — the pipeline would need to re-fetch all 1,279+ matches from scratch on every run (~20 min, ~1,300 API calls). Solving this requires one of:
- **Option B** — commit `matches.json` via Git LFS (file too large for standard git)
- **Option C** — store `matches.json` in cloud storage (S3, Supabase etc.) and sync in CI

Revisit when tournament cadence makes manual updates impractical.
