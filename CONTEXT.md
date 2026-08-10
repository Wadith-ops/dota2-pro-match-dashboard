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

**Tier 1 league** — the invite-level circuit, **as defined by [Liquipedia's Tier 1 Tournaments list](https://liquipedia.net/dota2/Tier_1_Tournaments)**. Scope is set by that external authority, not by OpenDota's `tier` field — which classes every league here as `professional`, alongside 2,468 others — and not by organiser name, which cannot separate DreamLeague Season 29 from DreamLeague Division 2 Season 3. Qualifiers, Division 2 circuits and regional events are excluded. See ADR-0001.

Called a **tournament** in the dashboard UI; *league* is the API's word and the column name (`league_name`). Both refer to the same thing.

**Ledger** — `data/leagues.json`, the record of every league OpenDota knows about and this project's verdict on each: `active`, `rejected` or `pending`. It replaced the hardcoded `ALL_LEAGUES` dict so that an excluded league is *visibly excluded* rather than merely absent — the distinction whose absence hid the Esports World Cup for two months. Verdicts are reversible: changing one word covers or drops a tournament on the next run. 10,050 leagues today, 15 of them active.

**Verdict** — what the ledger says about a league. **`active`** is fetched. **`rejected`** is a decision on record not to cover it — the great majority, and the state that makes absence legible. **`pending`** is a league OpenDota has listed since the ledger was seeded, awaiting a verdict; it is the only state that means "nobody has looked at this yet", which is why nothing is ever *seeded* pending.

**Seeding by existence** — the rule that makes `pending` mean something. Every league in OpenDota's response at seed time was decided then and there, so only ids appearing afterwards can be pending. Never by id range: The International 2013 is league `65006`, above every 2026 league, so an id cutoff would mark the whole back catalogue as new. See ADR-0001.

**Known gap** — a Tier 1 event on Liquipedia's list with no matching OpenDota league. Distinct from a rejected league: a gap is coverage the project *wants* and does not yet have. Distinct again from a pending league, which is data OpenDota has and the project has not yet judged. Recorded per event in `data/tier1_resolution.json`, as an entry whose `league_id` is null.

### Time

**Patch** — the game version a match was played on. The meta shifts materially across patches, so patch is a primary comparison axis.

Two columns carry it. **`patch_label`** is the name as the API gives it — `"7.39"`, `"7.40"`, `"7.41"` — written by the pipeline as a string and read back as one. It is the only form the dashboard reads: displayed, grouped, filtered and sorted. **`patch`** holds the same value, but `read_csv` re-infers it as a float and drops the trailing zero of `7.40`. Nothing in the dashboard touches it; it survives in the CSV for anything that wants the number, and displaying it is a bug.

A patch name is not guaranteed to be a number: `"Unknown"` is the label for a match with no patch, and a patch newer than the cached constants falls back to its raw id. So labels are ordered with `patch_sort_key`, never `float()` — which throws on the first — and never as plain strings, which would put `7.9` after `7.40`.

**The patch recorded is the gameplay patch, not the hotfix.** OpenDota's `/constants/patch` has 61 entries and every name is a plain `d.d`; hotfixes do not appear in it. A match played on 7.40c is therefore recorded as `7.40`. Valve's own patch list (`dota2.com/datafeed/patchnoteslist`, 117 entries) does carry `7.40b`, `7.41a` and the rest with release timestamps, so hotfix resolution by `start_time` is available if the finer grain is ever wanted — at the cost of splitting today's three patch buckets into nine, four of them under 65 matches. Not implemented.

**Game length / duration** — wall-clock match length. `duration_secs` is the raw value; `duration_mins` is the display value and the one used in charts.

### Objectives

**Roshan** — the neutral boss. Killing it drops the **Aegis**. `radiant_roshan_kills` / `dire_roshan_kills` count kills *by* that side. **First Roshan** (`first_roshan_time`, `first_roshan_team`) is tracked separately as a tempo signal.

**Aegis stolen / denied** — an Aegis picked up by the opposing team, or destroyed rather than taken. Rare; recorded per match, not per side.

**Tormentor** — a secondary neutral objective granting a shard. Counted per side.

**Barracks** — the buildings that upgrade an enemy's creeps when destroyed. Recorded as **lost**, from the owner's point of view: `radiant_barracks_lost` is the count Radiant *lost*, which is the count Dire *destroyed*. `team_barracks_killed` in the team-perspective grain flips this to the destroyer's point of view. Reading one as the other inverts the metric.

**Tower** — recorded the same way, `radiant_towers_lost` / `dire_towers_lost`: buildings destroyed, framed as losses by the owner.

**First blood** — the first hero kill of the match. `first_blood_time_mins` can be **negative, and negative values are valid data**: the match clock starts at zero at the horn, and teams contest runes and wards in the seconds before it. All 155 negative values in the dataset fall between −0.9 and −0.1 minutes, none below −1.0 — the pre-horn window exactly. Do not filter them. See ADR-0003, which records 133 of 1,605 as measured when it was written.

Zero is a time, not an absence. A first blood on the horn reads `first_blood_time_mins: 0.0`; only a match with no recorded first blood is null. Four matches were blanked by a truthiness test before `tier1-pipeline-automation/04`.

**Courier kill** — a killed courier. Per match, not per side.

### Kills

**Kill** always means a *hero* kill in this project. Roshan kills and building kills are named explicitly and never called "kills" unqualified. A side's hero kill count is its **score** (`radiant_score` / `dire_score`) — the API's naming, kept as-is.

### Derived concepts

**Four core metrics** — Roshan, Kills, Barracks, Game Length. Every tab reports on these four and no others. New analysis should extend them rather than introduce a fifth headline metric.

**Total** vs **team** — a `total_*` metric sums both sides for the match (`total_kills = radiant_score + dire_score`). A `team_*` metric is one side's contribution. Charts that show both label them explicitly, because the two differ by roughly 2x and are easy to misread.

**Both lost barracks** — at least one barracks lost by *each* side. A proxy for a decisive, drawn-out game rather than a one-sided stomp.

**Both teams Roshan** — at least one Roshan killed by each side.

**Non-Captain's-Mode match** — a match played on any draft format other than Captain's Mode (`game_mode` 2), carried on every row as the boolean `non_captains_mode`. Twelve exist today, all All Pick. The flag exists so a draft-sensitive metric can say what it excluded; the match itself is never dropped. An unreported `game_mode` flags too — unknown is not evidence of Captain's Mode.

**Over/under** — the calculator in the Head to Head and Drilldown tabs: given a threshold, what share of the filtered matches finished above it. The primary output for the betting-context audience.

### Pipeline

**Tier 1 event** — a tournament on [Liquipedia's Tier 1 Tournaments list](https://liquipedia.net/dota2/Tier_1_Tournaments). This, and not OpenDota's `professional` flag, is what defines the dataset's scope (ADR-0001). An *event* is Liquipedia's unit; a **league** is OpenDota's. They are not the same object and are never matched by name.

**Event window** — a Tier 1 event's start and end dates. The key everything resolves on, because the two sources name the same tournament differently — `BLAST SLAM VII` against `Blast Slam VII`, `PGL Wallachia Season 7` against `PGL Wallachia 2026 Season 7`. A league's matches falling inside an event's window is what identifies it.

**Tier 1 calendar** — every Tier 1 event with its name, window and prize pool, read from Liquipedia's rendered tournaments table. 324 events across 2005–2027; 13 in 2026. Obtained from the free MediaWiki API under its terms of use, cached for a day, with `data/tier1_calendar.json` as the committed fallback. See ADR-0006.

**Resolver** — the join between the two lists: which OpenDota league *is* a given Liquipedia Tier 1 event. It runs at the end of every pipeline run and writes its answer twice — as `tier1_event` on the league's ledger record, and per event in `data/tier1_resolution.json`. It never changes a verdict: recognising a tournament and deciding to cover it are two different acts, and the second is Wade's.

**Candidate** — a league whose matches *all* fall inside an event's window, within the ±2 day grace. Not some of them: a league spilling outside the window is a different tournament that happens to overlap. That single condition is what excludes qualifiers, Division 2 circuits and regional events without any rule naming them.

Leagues OpenDota marks `excluded` or `amateur` are not candidates — that is the source stating a league is not a competitive event, which is a different claim from `professional`, and the field is never used to rank or to select. See ADR-0009.

**Team overlap** — the first tiebreaker between two candidates in one window. It is the share of a league's **team slots** — two per match, so a team playing ten matches counts ten times — held by a team already in the dataset. An anonymous team keeps its slot in the denominator.

It is a *relative* ranking and can never be a threshold. Qualifiers outscore the events they qualify for, because Tier 1 teams play in their own qualifiers, and the correct winner scored 85.8% in one 2026 window against 74.5% in another. See ADR-0001.

**Window coverage** — the second tiebreaker: how much of the event's published window the league's matches actually span, from 0 to 1.

It exists because Tier 1 events **nest inside one another**. FISSURE PLAYGROUND 2 ran 23 October to 2 November 2025, entirely inside BLAST Slam IV's 14 October to 9 November, so both leagues are candidates for BLAST Slam IV and both are made of teams already tracked — 100% each. Team overlap cannot part them, and the nested event played *more* matches in *fewer* days, so ranking on volume picks the wrong one. Coverage picks the league that ran the window.

Like overlap it ranks and never gates. A tournament resolves on the day of its first match, when it covers one day of a twelve-day window; a minimum score would trade a wrong answer for no answer at all.

**Ambiguous window** — an event window holding more than one candidate. The highest overlap wins, and every candidate is recorded with its score so a wrong mapping can be seen before it enters the dataset. Three of the nine played 2026 events were ambiguous and all three resolved correctly, by margins of 46 to 91 percentage points.

**Awaiting resolution** — an event that has started, that the ledger does not yet map, and that began within the last year. These are the only events that cost API calls: they set how far back the run walks OpenDota's pro match list, and when there are none the resolver makes no OpenDota calls at all.

The year is a **lookback horizon**, not a tidy-up. Liquipedia's calendar reaches back to 2005 and this project has never mapped most of it, so without a horizon every event ever held is awaiting resolution and the first run walks twenty years of pro matches. Auditing the back catalogue is a deliberate, separate pass.

Only the **rendered tournaments table** is authoritative. The same page carries a Timeline template that deliberately includes Tier 2 events by the listed organisers; reading it is what once classified FISSURE Universe Episode 8 as Tier 1 and 1win Essence II as not. Episodes 4 and 6 of that series *are* Tier 1 — the distinction is per event, not per series.

**Checkpoint** — `checkpoints/fetched_matches.json`, the set of match IDs already fetched. Match-level only: league match-ID lists are always re-fetched so new matches are detected. An id missing from it means "fetch this", which is why a suspect match inside its retry window is deliberately left out.

**Raw match** — the full untouched API response, roughly 386 KB per match, of which `players` is 87% and `objectives` — the only part the dashboard reads — is 1.4%. Historically stored in `data/matches.json`; that store is being retired in favour of the Standard record, with the raw sink retained as a **disabled seam** so full-fidelity capture can be switched back on for modelling. Never committed.

**Standard record** — the modelling artifact: roughly 20 KB per match, retaining objectives in full, picks and bans, per-player aggregates, teamfight summaries, benchmarks, and the gold and XP advantage curves, while discarding per-player timeseries. Committed and versioned. Nothing consumes it yet — it accumulates so that modelling does not require a full re-fetch later. See ADR-0002. (Lands in `tier1-pipeline-automation/10`.)

**Flat CSV** — `data/matches_flat.csv`, the flattened match-row export. This is the only data the deployed dashboard reads.

**Suspect match** — a match whose objective data is missing or implausible: empty `objectives`, zero towers lost by both sides in a match over 20 minutes, or a combined hero kill score of zero. A zero that means "unknown" corrupts every average it touches, so suspect matches are flagged on the row as `is_suspect` and `suspect_reason`, **excluded from every figure**, and kept visible in match history with the reason shown. Five exist today, all in DreamLeague Season 29, including a 96-minute game recorded as zero towers.

The exclusion is from *figures*, not from the dataset: the match count, the coverage line and every match history table still hold them, and a head-to-head record still counts them, because who won is right whether or not the replay parsed. See ADR-0007.

**Unparsed replay** — the cause. OpenDota answers a match request as soon as the match ends, sometimes before it has parsed the replay, and an unparsed match arrives with no `objectives` at all. Such a match is held out of the checkpoint so the next run fetches it again, for five days after its start time; after that it is checkpointed, recorded in `checkpoints/unparsed_matches.json`, and left alone. A match that parses in the meantime is re-flattened and loses its flag with no manual step.

**Permanently unparsed** — a suspect match that has run out of retries. The distinction from a merely suspect one is operational, not analytical: both are excluded from figures and shown in match history identically, and the difference is only whether the pipeline will try again. It is recorded in the checkpoint rather than on the row, because it describes the pipeline's state on a given day rather than the match.

**Retry window** — the five days a suspect match stays eligible for re-fetching, measured from the **match's own start time** rather than from when the pipeline first saw it. A backfilled event is therefore past its window on arrival, which is correct: its replays were parsed long ago or never.

**Coverage** — what the dataset currently contains: generation timestamp, match count, tournament count, excluded count, and the date of the most recent match. Written to `data/meta.json` and shown on the dashboard, so a gap is visible at the point of use rather than discovered later.

The **latest match date** is the load-bearing field. A run that fetches nothing still refreshes the generation timestamp, so generation time alone cannot reveal a gap — only the match date can. The dashboard shows both.

## Leagues covered

The authoritative list is the `active` entries in `data/leagues.json`; this table mirrors them for reading. When the two disagree, the ledger wins and the table is wrong.

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
| 19785 | Esports World Cup 2026      |
| 20009 | 1win Essence II             |
| 19719 | The International 2026      |

The International 2026 is configured but holds **zero matches** — it starts 13 Aug 2026, and its matches are picked up on the first daily run after that. A configured league with no matches is normal, not a fault.

A league's **name here is the name the dashboard shows**, and it comes from the ledger rather than from OpenDota — which calls league 17419 "SLAM IV" where the dataset has said "Slam IV" for 96 matches. The pipeline writes the ledger's name onto every match row as `league_name`, so changing it in the ledger renames the tournament everywhere the next time the CSV is rebuilt, and leaving it to OpenDota would let a rebrand split one tournament into two.

Tournaments are ordered by **first match date, descending** — latest first — everywhere they are listed. Never alphabetically.

## Column dictionary (`matches_flat.csv`)

**Match info:** `match_id`, `league_id`, `league_name`, `patch`, `patch_label`, `start_time`, `duration_secs`, `duration_mins`, `radiant_win`, `radiant_score`, `dire_score`, `game_mode`, `non_captains_mode`

**Teams:** `radiant_team_id`, `radiant_team_name`, `dire_team_id`, `dire_team_name`

**Roshan:** `radiant_roshan_kills`, `dire_roshan_kills`, `first_roshan_time`, `first_roshan_time_mins`, `first_roshan_team`

**Aegis:** `aegis_stolen`, `aegis_denied`

**Tormentors:** `radiant_tormentor_kills`, `dire_tormentor_kills`

**Buildings:** `radiant_towers_lost`, `dire_towers_lost`, `radiant_barracks_lost`, `dire_barracks_lost`

**Other:** `first_blood_time`, `first_blood_time_mins`, `courier_kills`

**Data quality:** `is_suspect`, `suspect_reason` — the flag, and the reasons behind it joined by `;` (`no_objectives`, `no_towers_lost`, `no_hero_kills`). Blank on an ordinary match, and blank reads back from the CSV as NaN whatever dtype is asked for, so `load_data()` fills it.

### Added in `load_data()`

`total_roshan`, `total_kills`, `total_barracks`, `total_towers`, `both_lost_barracks`, `both_teams_roshan`

`patch_label`, `is_suspect` and `suspect_reason` are **not** among them — they come from the CSV, and `load_data()` passes `core.CSV_DTYPES` to `read_csv` so the string columns arrive as strings.

### Added in `build_team_perspective()`

`team_won`, `side`, `got_first_roshan`, `team_kills`, `team_barracks_killed`, plus the match-level columns carried through unchanged — including `is_suspect`, so a team-level figure can exclude the same matches a match-level one does.

## Invariants

- One row per match in `matches_flat.csv`; exactly two team-perspective rows per match.
- `total_kills = radiant_score + dire_score` — always recomputed, never read from the API.
- A team-perspective row's `team_barracks_killed` equals the *opponent's* `*_barracks_lost`.
- `game_mode` is overwhelmingly `2` (Captain's Mode); 12 matches are mode 1, and each carries `non_captains_mode: True`. Matches are **flagged, never dropped**, for their game mode. Any metric sensitive to draft format should filter on the flag or state that it doesn't.
- `patch_label` is a non-empty string in every row — `"Unknown"` where the API reported no patch. That is what lets it survive the CSV as a label rather than a number.
- `first_blood_time_mins < 0` is **valid data** — a pre-horn kill, not an artefact. See ADR-0003.
- An objective timing of `0` means the event happened on the horn; `null` means no such event was recorded. The two are never conflated.
- A suspect match is excluded from figures but present in match history. Absence from a chart never means absence from the record, and `meta.json` states the excluded count so the dashboard can say how many. `excluded_count: null` means the figure was never computed — a `meta.json` older than `tier1-pipeline-automation/05` — and reads as "unknown", never as zero.
