# Context: Dota 2 Pro Match Analysis

The glossary and domain model for this project. `CLAUDE.md` holds the rules and operational facts; this file holds the *language*. When naming a concept in a chart label, column, issue title, or test, use the term as defined here.

## What this project is about

Tier 1 professional Dota 2 matches, pulled from the OpenDota API and aggregated into objective-level statistics. The audience is someone forming expectations about a match before it happens — typically around over/under lines on kills, game length, Roshans, and barracks. The project describes what *has* happened; it does not predict what *will* happen (see Out of Scope in `CLAUDE.md`).

## The two grains

Almost every bug in this codebase comes from confusing these. They are the central modelling concept.

**Match row** — one row per match, the shape of `matches_flat.csv`. Both teams appear in a single row, distinguished by `radiant_*` / `dire_*` column prefixes. This is the correct grain for anything describing *the match as a whole*: total kills, game length, whether both teams lost barracks.

**Team-perspective row** — one row per *team per match*, produced by pivoting match rows in `build_team_perspective()`. A match becomes two rows. This is the correct grain for anything describing *a team*: win rate, average kills by that team, side splits.

The consequence: a distribution chart (histogram, box, violin) over a match-level metric must be built from match rows. Building it from team-perspective rows double-counts every match and silently narrows the distribution.

## Glossary

### Sides and teams

**Radiant / Dire** — the two sides of the map. Side assignment is per-match and carries a real advantage, so it is worth splitting on.

**Anonymous team** — a match where OpenDota has no team name and falls back to the literal strings `"Radiant"` / `"Dire"`. These are not teams. They are excluded from every team-level calculation via `ANON_NAMES`, but their matches remain valid for match-level metrics.

**Tier 1 league** — the invite-level circuit. This project tracks a fixed, hand-curated set (`ALL_LEAGUES` in the pipeline), not everything OpenDota classes as professional. Called a **tournament** in the dashboard UI; *league* is the API's word and the column name (`league_name`). Both refer to the same thing.

### Time

**Patch** — the game version a match was played on. The meta shifts materially across patches, so patch is a primary comparison axis. Stored as a float (`7.39`, `7.4`, `7.41`) and displayed via `patch_label`, which restores the trailing zero (`7.4` → `"7.40"`).

**Game length / duration** — wall-clock match length. `duration_secs` is the raw value; `duration_mins` is the display value and the one used in charts.

### Objectives

**Roshan** — the neutral boss. Killing it drops the **Aegis**. `radiant_roshan_kills` / `dire_roshan_kills` count kills *by* that side. **First Roshan** (`first_roshan_time`, `first_roshan_team`) is tracked separately as a tempo signal.

**Aegis stolen / denied** — an Aegis picked up by the opposing team, or destroyed rather than taken. Rare; recorded per match, not per side.

**Tormentor** — a secondary neutral objective granting a shard. Counted per side.

**Barracks** — the buildings that upgrade an enemy's creeps when destroyed. Recorded as **lost**, from the owner's point of view: `radiant_barracks_lost` is the count Radiant *lost*, which is the count Dire *destroyed*. `team_barracks_killed` in the team-perspective grain flips this to the destroyer's point of view. Reading one as the other inverts the metric.

**Tower** — recorded the same way, `radiant_towers_lost` / `dire_towers_lost`: buildings destroyed, framed as losses by the owner.

**First blood** — the first hero kill of the match. `first_blood_time_mins` can be **negative** — a pre-horn artefact of the API — and must be filtered before use.

**Courier kill** — a killed courier. Per match, not per side.

### Kills

**Kill** always means a *hero* kill in this project. Roshan kills and building kills are named explicitly and never called "kills" unqualified. A side's hero kill count is its **score** (`radiant_score` / `dire_score`) — the API's naming, kept as-is.

### Derived concepts

**Four core metrics** — Roshan, Kills, Barracks, Game Length. Every tab reports on these four and no others. New analysis should extend them rather than introduce a fifth headline metric.

**Total** vs **team** — a `total_*` metric sums both sides for the match (`total_kills = radiant_score + dire_score`). A `team_*` metric is one side's contribution. Charts that show both label them explicitly, because the two differ by roughly 2x and are easy to misread.

**Both lost barracks** — at least one barracks lost by *each* side. A proxy for a decisive, drawn-out game rather than a one-sided stomp.

**Both teams Roshan** — at least one Roshan killed by each side.

**Over/under** — the calculator in the Head to Head and Drilldown tabs: given a threshold, what share of the filtered matches finished above it. The primary output for the betting-context audience.

### Pipeline

**Checkpoint** — `checkpoints/fetched_matches.json`, the set of match IDs already fetched. Match-level only: league match-ID lists are always re-fetched so new matches are detected.

**Raw match** — the full untouched API response, stored in `data/matches.json`. Local only. It holds hero and player data not yet extracted.

**Flat CSV** — `data/matches_flat.csv`, the flattened match-row export. This is the only data the deployed dashboard reads.

## Leagues covered

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
| 19696 | DreamLeague Season 29       |
| 19101 | Blast Slam VII              |

Tournaments are ordered by **first match date, descending** — latest first — everywhere they are listed. Never alphabetically.

## Column dictionary (`matches_flat.csv`)

**Match info:** `match_id`, `league_id`, `league_name`, `patch`, `start_time`, `duration_secs`, `duration_mins`, `radiant_win`, `radiant_score`, `dire_score`, `game_mode`

**Teams:** `radiant_team_id`, `radiant_team_name`, `dire_team_id`, `dire_team_name`

**Roshan:** `radiant_roshan_kills`, `dire_roshan_kills`, `first_roshan_time`, `first_roshan_time_mins`, `first_roshan_team`

**Aegis:** `aegis_stolen`, `aegis_denied`

**Tormentors:** `radiant_tormentor_kills`, `dire_tormentor_kills`

**Buildings:** `radiant_towers_lost`, `dire_towers_lost`, `radiant_barracks_lost`, `dire_barracks_lost`

**Other:** `first_blood_time`, `first_blood_time_mins`, `courier_kills`

### Added in `load_data()`

`total_roshan`, `total_kills`, `total_barracks`, `both_lost_barracks`, `both_teams_roshan`, `patch_label`

### Added in `build_team_perspective()`

`team_won`, `side`, `got_first_roshan`, `team_kills`, `team_barracks_killed`, plus the match-level columns carried through unchanged.

## Invariants

- One row per match in `matches_flat.csv`; exactly two team-perspective rows per match.
- `total_kills = radiant_score + dire_score` — always recomputed, never read from the API.
- A team-perspective row's `team_barracks_killed` equals the *opponent's* `*_barracks_lost`.
- `game_mode` is overwhelmingly `2` (Captain's Mode). Any metric sensitive to draft format should filter to it or state that it doesn't.
- `first_blood_time_mins < 0` is invalid data, not a fast first blood.
