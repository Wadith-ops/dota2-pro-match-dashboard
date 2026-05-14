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
**Status: ✅ Complete — shipped 2026-05-02**

Replaced single-page Roshan analysis with a 3-tab layout focused on four core metrics:
**Roshan**, **Kills**, **Barracks**, **Game Length**.

### Layout
- Page title: `"Dota 2 Pro Match Analysis"`
- Sidebar team filter defaults to empty (all teams) — select specific teams to drill in
- Anonymous teams ("Radiant"/"Dire") filtered from all team-perspective calculations

### Tab 1 — Team
- 9 KPIs: avg team roshans, team 2+ roshans %, avg total roshans, match 3+ roshans %, avg team kills, avg total kills, avg team barracks, both-lost-barracks %, avg game length
- Each metric (Roshan / Kills / Barracks) shown as grouped bars — team vs total — broken down by patch and by tournament side-by-side
- Barracks section includes a separate "% games both teams lost barracks" chart (patch + tournament)
- Game length: distribution histogram + box plot by patch

### Tab 2 — Tournament
- Summary stats table per tournament (N matches, date range, avg roshans, 3+ roshans %, avg kills, avg barracks, both-lost %, avg duration)
- 4 comparison bar charts: roshans / kills / barracks / avg duration

### Tab 3 — Meta Trends
- Per-patch KPI columns (one column per patch, all 6 metrics)
- 4 comparison bar charts by patch: roshans / kills / barracks / game length violin

### Key derived columns added in `load_data()`
- `total_kills = radiant_score + dire_score`
- `total_barracks = radiant_barracks_lost + dire_barracks_lost`
- `both_lost_barracks = (radiant_barracks_lost >= 1) & (dire_barracks_lost >= 1)`

### `build_team_perspective()` additions
- `team_kills` — team's own hero kills (radiant_score or dire_score depending on side)
- `team_barracks_killed` — barracks the team destroyed (opponent's barracks_lost)
- `total_kills`, `total_barracks`, `both_lost_barracks` — match-level columns carried through

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
