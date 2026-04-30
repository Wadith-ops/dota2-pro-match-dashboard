# Project Plan: Pivot from EDA to Public Dashboard

## Context
Originally an EDA / data science project. Pivoting to a **public-facing Streamlit dashboard** that others can view, with pipeline automation planned for future tournaments.

---

## Phase 1: Repo + Hosting Setup
**Status: Not started**

1. Create `.gitignore` — must exclude `data/matches.json` (705 MB), `checkpoints/`, `__pycache__/`, `.claude/`, `.venv/`
2. Create `requirements.txt` — pinned: `streamlit`, `plotly`, `pandas`, `requests`
3. `git init`, commit only: `.gitignore`, `requirements.txt`, `CLAUDE.md`, `PLAN.md`, `dashboard.py`, `opendota_pipeline.py`, `data/matches_flat.csv`
4. Push to a new public GitHub repo (suggested name: `dota2-pro-match-dashboard`)
5. Deploy on Streamlit Community Cloud (share.streamlit.io) — connect repo, set main file = `dashboard.py`

No secrets needed — the CSV is bundled in the repo. Streamlit Cloud redeploys automatically on every push to `main`.

---

## Phase 2: Dashboard Expansion
**Status: Not started**

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
**Status: Design only — implement when next tournament starts**

GitHub Actions workflow (`.github/workflows/update_data.yml`):
- Trigger: `workflow_dispatch` (manual) or `schedule: cron "0 6 * * *"` (daily 6am UTC)
- Steps: checkout → setup Python → run pipeline → commit updated CSV with `[skip ci]` → push

**Two pipeline fixes required before this works:**
1. Remove `os.chdir(r"C:\Users\Wade\...")` on line 4 of `opendota_pipeline.py`
2. Make `SAVE_RAW` read from env var: `os.getenv("SAVE_RAW", "true").lower() == "true"` — set `false` in CI to avoid writing 700 MB file
