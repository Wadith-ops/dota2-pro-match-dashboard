"""
The pure transform core.

Every function here takes plain data and returns plain data. No network calls,
no file reads or writes, no checkpointing, no git, no Streamlit — those live in
`opendota_pipeline.py` and `dashboard.py`. That separation is what makes the
pipeline testable: `tests/` exercises this module against recorded payloads and
never goes near the API.

Importing this module has no side effects.
"""
import json
import re
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser

# The draft format the dataset is overwhelmingly played on. Anything else is
# flagged on the row — never dropped from it.
CAPTAINS_MODE = 2

# The label for a match whose patch the API did not report. Every row carries a
# patch label, so the column is a string throughout and survives the CSV.
UNKNOWN_PATCH = "Unknown"

# How the CSV must be read back. `patch_label` is written as "7.40" and pandas
# would otherwise re-infer it as the float 7.4, losing the trailing zero — the
# thing this column exists to preserve. `suspect_reason` is blank on most rows,
# and blank reads back as NaN rather than "" — consumers fill it. Passed to
# `read_csv` by the dashboard.
CSV_DTYPES = {"patch_label": str, "suspect_reason": str}

_PATCH_NAME = re.compile(r"^(\d+)\.(\d+)(.*)$")


def patch_sort_key(label):
    """
    Orders patch labels by release.

    Not every label is a number. "Unknown" is the label for a match with no
    patch, and a patch newer than the cached constants falls back to its raw
    id — `float()` throws on the first and misplaces the second. Sorting the
    labels as plain strings is no better: it puts "7.9" after "7.40".

    Lettered hotfix names ("7.40b") sort directly after the patch they revise.
    OpenDota does not report them today — its constants are gameplay-patch
    granularity only — so that branch is there for the day the pipeline
    resolves hotfixes, not for data it currently sees.
    """
    parsed = _PATCH_NAME.match(str(label).strip())
    if not parsed:
        return (1, 0, 0, str(label))

    major, minor, revision = parsed.groups()
    return (0, int(major), int(minor), revision)


def flatten_objectives(objectives):
    """
    Extracts and counts all objective types from the objectives array.
    Returns a dictionary of counts and timings.

    A missing or empty array yields zero counts rather than an error — the
    caller cannot tell "nothing happened" from "nothing was recorded" here.
    Distinguishing the two is the job of tier1-pipeline-automation/05.
    """
    result = {
        # Roshan
        "radiant_roshan_kills"   : 0,
        "dire_roshan_kills"      : 0,
        "first_roshan_time"      : None,
        "first_roshan_team"      : None,
        # Aegis
        "aegis_stolen"           : 0,
        "aegis_denied"           : 0,
        # Tormentor
        "radiant_tormentor_kills": 0,
        "dire_tormentor_kills"   : 0,
        # Buildings
        "radiant_towers_lost"    : 0,
        "dire_towers_lost"       : 0,
        "radiant_barracks_lost"  : 0,
        "dire_barracks_lost"     : 0,
        # Other
        "first_blood_time"       : None,
        "courier_kills"          : 0,
    }

    if not objectives:
        return result

    roshan_seen = False

    for obj in objectives:
        obj_type = obj.get("type")
        team     = obj.get("team")
        time     = obj.get("time")
        key      = obj.get("key", "")

        # ── Roshan kills ──────────────────────────────────────
        if obj_type == "CHAT_MESSAGE_ROSHAN_KILL":
            if team == 2:
                result["radiant_roshan_kills"] += 1
            elif team == 3:
                result["dire_roshan_kills"] += 1
            if not roshan_seen:
                result["first_roshan_time"] = time
                result["first_roshan_team"] = "radiant" if team == 2 else "dire"
                roshan_seen = True

        # ── Aegis ─────────────────────────────────────────────
        elif obj_type == "CHAT_MESSAGE_AEGIS_STOLEN":
            result["aegis_stolen"] += 1

        elif obj_type == "CHAT_MESSAGE_DENIED_AEGIS":
            result["aegis_denied"] += 1

        # ── Tormentor ─────────────────────────────────────────
        elif obj_type == "CHAT_MESSAGE_MINIBOSS_KILL":
            if team == 2:
                result["radiant_tormentor_kills"] += 1
            elif team == 3:
                result["dire_tormentor_kills"] += 1

        # ── Buildings (use key field, not team) ───────────────
        elif obj_type == "building_kill":
            if "goodguys" in key:
                team_killed = "radiant"
            elif "badguys" in key:
                team_killed = "dire"
            else:
                continue

            if "tower" in key:
                if team_killed == "radiant":
                    result["radiant_towers_lost"] += 1
                else:
                    result["dire_towers_lost"] += 1
            elif "rax" in key:
                if team_killed == "radiant":
                    result["radiant_barracks_lost"] += 1
                else:
                    result["dire_barracks_lost"] += 1

        # ── First blood ───────────────────────────────────────
        elif obj_type == "CHAT_MESSAGE_FIRSTBLOOD":
            result["first_blood_time"] = time

        # ── Courier ───────────────────────────────────────────
        elif obj_type == "CHAT_MESSAGE_COURIER_LOST":
            result["courier_kills"] += 1

    return result


# ── Suspect matches ───────────────────────────────────────────────────────────
#
# OpenDota answers a match request as soon as the match ends, whether or not it
# has parsed the replay yet. An unparsed match arrives with its objectives
# missing, so every objective column flattens to zero — and a zero that means
# "unknown" is indistinguishable from a zero that means "nothing happened". One
# such match is 96 minutes long with 130 hero kills and no towers.
#
# These matches are flagged rather than dropped: the averages exclude them, the
# match history keeps them, and the pipeline re-fetches them for a few days in
# case the replay is parsed late. See ADR-0007.

SUSPECT_NO_OBJECTIVES = "no_objectives"
SUSPECT_NO_TOWERS     = "no_towers_lost"
SUSPECT_NO_KILLS      = "no_hero_kills"

# What a fetched match is: done with, worth fetching again, or given up on.
FETCH_COMPLETE = "complete"
FETCH_HELD     = "held"
FETCH_UNPARSED = "unparsed"

# Below this, a game with no buildings destroyed is an ordinary early forfeit.
TOWERLESS_MATCH_SECS = 20 * 60

# How long a suspect match stays eligible for re-fetching, measured from the
# match's own start time. At the six-hourly cadence that is twenty attempts,
# which is generous — a replay unparsed for five days is not going to parse.
SUSPECT_RETRY_DAYS = 5


def suspect_reasons(match, objective_counts=None):
    """
    Names every reason this match's objective data cannot be trusted, as a
    tuple. An empty tuple is a match whose numbers stand.

    `objective_counts` is the output of `flatten_objectives` when the caller has
    already computed it; left out, it is computed here. Either way the verdict
    is the same — it is a parameter to save the second pass, not to change the
    answer.

    A missing objectives array is reported once. Such a match also has no towers
    in it, but that zero *is* the missing array, not separate corroboration; the
    tower rule is there for a match that recorded objectives and still shows no
    buildings, which is an independent signal.
    """
    objectives = match.get("objectives")
    if not objectives:
        return (SUSPECT_NO_OBJECTIVES,)

    if objective_counts is None:
        objective_counts = flatten_objectives(objectives)

    reasons = []

    duration = match.get("duration")
    towers_lost = (objective_counts.get("radiant_towers_lost", 0)
                   + objective_counts.get("dire_towers_lost", 0))
    if duration is not None and duration > TOWERLESS_MATCH_SECS and towers_lost == 0:
        reasons.append(SUSPECT_NO_TOWERS)

    # An unreported score is not a goalless game; it is a thin payload, which is
    # the condition being caught.
    if (match.get("radiant_score") or 0) + (match.get("dire_score") or 0) == 0:
        reasons.append(SUSPECT_NO_KILLS)

    return tuple(reasons)


def retry_window_open(match, now):
    """
    Whether a suspect match is still young enough to be worth re-fetching.

    The deadline hangs off the match's own start time rather than off when the
    pipeline first saw it. A match is fetched within hours of ending, so the two
    are the same for anything arriving live — and for a backfill they are not:
    an event imported two months late is past its deadline immediately, which is
    the right answer, since its replays were either parsed long ago or never.

    Keeping the anchor in the payload is also what lets this stay pure: no
    ledger of first-seen timestamps has to be carried between runs.
    """
    start_time = match.get("start_time")
    if start_time is None:
        return False

    return now.timestamp() < start_time + SUSPECT_RETRY_DAYS * 86400


def classify_fetch(match, now):
    """
    What the pipeline should do with a match it has just fetched.

    - `FETCH_COMPLETE` — the numbers stand; check it off and never fetch again.
    - `FETCH_HELD` — suspect, but young enough that OpenDota may still parse the
      replay. It must be left *out* of the completed checkpoint: that absence is
      the whole re-fetch mechanism, since the next run fetches every id it has
      no record of having fetched.
    - `FETCH_UNPARSED` — suspect and out of time. Check it off like any other
      match; a replay unparsed for five days is not going to be parsed, and
      re-fetching it every six hours forever spends calls on nothing.

    The clock is a parameter because reading it is I/O. Which of these three
    answers means "add to the checkpoint" is the shell's business, but *which
    answer* is not, and this is where it is decided and tested.
    """
    if not suspect_reasons(match):
        return FETCH_COMPLETE

    return FETCH_HELD if retry_window_open(match, now) else FETCH_UNPARSED


def flatten_match(match, patch_map):
    """
    Flattens a single raw match dictionary into a flat row for the DataFrame.

    `patch_map` is passed in rather than read from a module global, because
    building it is a network call. Injecting it is what lets this run offline.
    """
    # ── Patch mapping ─────────────────────────────────────────
    # `patch_label` is the display form and is written here rather than rebuilt
    # downstream, so "7.40" keeps its trailing zero instead of arriving as 7.4.
    # `is not None`, not truthiness: patch id 0 is 6.70, a real patch.
    raw_patch = match.get("patch")
    patch     = (patch_map.get(raw_patch, str(raw_patch))
                 if raw_patch is not None else None)

    # ── Duration conversions ──────────────────────────────────
    duration_secs = match.get("duration")
    duration_mins = round(duration_secs / 60, 1) if duration_secs else None

    game_mode = match.get("game_mode")

    # ── Flat fields ───────────────────────────────────────────
    row = {
        "match_id"          : match.get("match_id"),
        "league_id"         : match.get("leagueid"),
        "league_name"       : match.get("league_name"),
        "patch"             : patch,
        "patch_label"       : patch if patch is not None else UNKNOWN_PATCH,
        "start_time"        : match.get("start_time"),
        "duration_secs"     : duration_secs,
        "duration_mins"     : duration_mins,
        "radiant_win"       : match.get("radiant_win"),
        "radiant_score"     : match.get("radiant_score"),
        "dire_score"        : match.get("dire_score"),
        "game_mode"         : game_mode,
        # Flagged, not filtered. A draft-sensitive metric can exclude these
        # rows deliberately; nothing here decides that for it. An unreported
        # mode is not evidence of Captain's Mode, so it flags too.
        "non_captains_mode" : game_mode != CAPTAINS_MODE,
    }

    # ── Team fields ───────────────────────────────────────────
    radiant_team = match.get("radiant_team") or {}
    dire_team    = match.get("dire_team")    or {}

    row["radiant_team_id"]   = radiant_team.get("team_id")
    row["radiant_team_name"] = radiant_team.get("name", "Radiant")
    row["dire_team_id"]      = dire_team.get("team_id")
    row["dire_team_name"]    = dire_team.get("name", "Dire")

    # ── Objectives ────────────────────────────────────────────
    obj_data = flatten_objectives(match.get("objectives"))

    # ── Convert objective timings to minutes ──────────────────
    # `is not None`, not truthiness: an objective at exactly t=0 is a real
    # event on the horn, and testing truthiness blanked it. Negative timings
    # are real too — a pre-horn first blood keeps its sign (ADR-0003).
    for time_field in ["first_roshan_time", "first_blood_time"]:
        raw_time = obj_data.get(time_field)
        obj_data[f"{time_field}_mins"] = (
            round(raw_time / 60, 1) if raw_time is not None else None
        )

    row.update(obj_data)

    # ── Data quality ──────────────────────────────────────────
    # Last, because the verdict reads the counts above. The counts are passed
    # in rather than recomputed: this is the same objectives array, read once.
    reasons = suspect_reasons(match, obj_data)
    row["is_suspect"]     = bool(reasons)
    row["suspect_reason"] = ";".join(reasons)

    return row


def index_by_match_id(matches):
    """
    Where each match sits in a list of stored payloads.

    The pipeline appends every match it fetches, and a match held back for
    re-fetching is fetched more than once. Without this index the second copy
    would be appended alongside the first and the match would be counted twice
    in every average — the failure this whole issue exists to prevent, arriving
    by the back door.
    """
    return {
        match.get("match_id"): position
        for position, match in enumerate(matches)
        if match.get("match_id") is not None
    }


def store_match(matches, positions, match):
    """
    Stores a fetched match in place, replacing any payload already held for its
    id, and keeps `positions` in step. Both arguments are updated.

    The replacement is always the better record: the only match fetched twice is
    one held back as unparsed, and the second fetch is the one with the replay.
    """
    match_id = match.get("match_id")
    position = positions.get(match_id)

    if position is None:
        positions[match_id] = len(matches)
        matches.append(match)
    else:
        matches[position] = match


def build_rows(matches, patch_map):
    """
    Flattens every raw match into a match row. One row per match.
    """
    return [flatten_match(match, patch_map) for match in matches]


def coverage_meta(rows, generated_at):
    """
    Records what the dataset currently holds, so the dashboard can state its own
    coverage rather than leaving a gap to be discovered two months later.

    latest_match_date is the staleness signal that matters: a run which fetches
    nothing still refreshes generated_at, so only the match date reveals a gap.

    excluded_count is how many rows are suspect — flagged, kept in the dataset,
    and left out of every average. It is a real count now that classification
    exists; it read null while the answer was "not yet computed", because 0
    would have claimed "computed, none found" with five in the dataset.

    `generated_at` is a datetime the caller supplies, because reading the clock
    is I/O and this function does none.
    """
    start_times = [row.get("start_time") for row in rows]
    played      = [ts for ts in start_times if ts is not None]

    latest = (
        datetime.fromtimestamp(max(played), tz=timezone.utc).strftime("%Y-%m-%d")
        if played
        else None
    )

    return {
        "generated_at"     : generated_at.isoformat(timespec="seconds"),
        "match_count"      : len(rows),
        "tournament_count" : len({row.get("league_name") for row in rows
                                  if row.get("league_name") is not None}),
        "excluded_count"   : sum(1 for row in rows if row.get("is_suspect")),
        "latest_match_date": latest,
    }


# ── The league ledger ─────────────────────────────────────────────────────────
#
# Coverage used to be a dict in the pipeline. A dict can say which leagues to
# fetch and nothing else, so a league considered and rejected read exactly like
# one nobody had ever heard of — which is why the Esports World Cup's absence
# was invisible for two months (ADR-0001).
#
# `data/leagues.json` replaces it with a verdict per league. Every league
# OpenDota knows about carries one, so "not covered" is a decision on record
# rather than a gap in a literal, and reversing a decision is editing one word.
#
# The functions below read and update that document. Reading and writing the
# file is the pipeline shell's job.

LEDGER_ACTIVE   = "active"
LEDGER_REJECTED = "rejected"
LEDGER_PENDING  = "pending"

LEDGER_VERDICTS = (LEDGER_ACTIVE, LEDGER_REJECTED, LEDGER_PENDING)

# The name a league is logged and recorded under when the ledger gives none.
UNKNOWN_LEAGUE = "Unknown League"


def ledger_entries(ledger):
    """
    Every league record in the ledger, in file order.

    A ledger that is missing, empty or the wrong shape reads as no leagues
    rather than raising. It is a hand-edited file: it can arrive broken, and
    every caller here would rather report that than fail mid-run.
    """
    if not isinstance(ledger, dict):
        return []

    entries = ledger.get("leagues")
    if not isinstance(entries, list):
        return []

    return [entry for entry in entries if isinstance(entry, dict)]


def active_leagues(ledger):
    """
    The leagues to fetch, as `{league_id: name}` in ledger order.

    The name is the ledger's, not OpenDota's. It is written onto every match row
    as `league_name` and grouped on by the dashboard, so a league OpenDota
    renames — or spells "SLAM IV" where the dataset has said "Slam IV" for 96
    matches — must not fracture a season's rows into two tournaments.

    A record with no id is skipped; one with no name is fetched under a
    placeholder. Losing the label costs a readable log line, and losing the
    league would be exactly the silent exclusion the ledger exists to end.
    """
    fetched = {}

    for entry in ledger_entries(ledger):
        league_id = entry.get("league_id")

        if league_id is None or entry.get("verdict") != LEDGER_ACTIVE:
            continue

        # First entry wins, so a duplicated id resolves the same way every run
        # rather than depending on which copy was appended last.
        fetched.setdefault(league_id, entry.get("name") or UNKNOWN_LEAGUE)

    return fetched


def verdict_counts(ledger):
    """How many leagues hold each verdict — the run's one-line coverage report."""
    counts = {}

    for entry in ledger_entries(ledger):
        verdict = entry.get("verdict")
        counts[verdict] = counts.get(verdict, 0) + 1

    return counts


def ledger_problems(ledger):
    """
    Everything wrong with the ledger, described one line at a time.

    The file is reviewed and edited in pull requests, so it can arrive
    misspelled — and a misspelled verdict fails the way a rejection does: the
    league is simply not fetched. That is the silent exclusion this whole
    ledger exists to end, so the run says what it could not read.
    """
    problems = []
    seen     = set()

    for entry in ledger_entries(ledger):
        league_id = entry.get("league_id")
        name      = entry.get("name") or UNKNOWN_LEAGUE

        if league_id is None:
            problems.append(f"entry '{name}' has no league_id and is ignored")
            continue

        if league_id in seen:
            problems.append(
                f"league_id {league_id} ({name}) appears more than once — "
                f"the first entry decides"
            )
        seen.add(league_id)

        verdict = entry.get("verdict")
        if verdict not in LEDGER_VERDICTS:
            problems.append(
                f"league_id {league_id} ({name}) has verdict '{verdict}', "
                f"which is not one of {'/'.join(LEDGER_VERDICTS)} — it will "
                f"not be fetched"
            )

    return problems


def _ledger_entry(league_id, name, verdict, tier1_event=None):
    """One ledger record, with its keys in the order the file reads best."""
    return {
        "league_id"  : league_id,
        "name"       : name,
        "tier1_event": tier1_event,
        "verdict"    : verdict,
    }


def _unseen_leagues(api_leagues, seen):
    """
    Each league in an OpenDota response whose id is new to `seen`, as
    `(league_id, name)`. `seen` is updated as it goes.

    Shared by seeding and discovery so that neither can write a second entry
    for one league: a response that omits an id, or repeats one, is the API's
    business rather than the ledger's.
    """
    for league in api_leagues or []:
        league_id = league.get("leagueid")

        if league_id is None or league_id in seen:
            continue
        seen.add(league_id)

        yield league_id, league.get("name")


def seed_ledger(api_leagues, active_names=None, seeded_on=None):
    """
    Builds the ledger from OpenDota's full league list.

    Seeding is **by existence**: every league in the response is decided here
    and now. `active_names` is `{league_id: name}` — keyed by id, because names
    are never how a league is identified (ADR-0001); the name in it is only the
    label to record. Those leagues are seeded `active`, everything else
    `rejected`. Nothing is seeded `pending`, because `pending` has to mean
    "appeared since the ledger was written", and it cannot mean that if ten
    thousand leagues are born pending.

    Not by date and not by id range. League ids are not chronological — The
    International 2013 is 65006, above every 2026 league — so an id cutoff
    would mark the entire back catalogue as new.

    A league in `active_names` that the response omits is still recorded, so a
    short API answer cannot quietly drop a tournament the project covers.

    `seeded_on` is passed in rather than read from a clock, because reading the
    clock is I/O and this function does none.
    """
    active_names = active_names or {}
    entries      = []
    seen         = set()

    for league_id, api_name in _unseen_leagues(api_leagues, seen):
        covered = league_id in active_names
        entries.append(_ledger_entry(
            league_id,
            active_names[league_id] if covered else api_name,
            LEDGER_ACTIVE if covered else LEDGER_REJECTED,
        ))

    for league_id, name in active_names.items():
        if league_id not in seen:
            entries.append(_ledger_entry(league_id, name, LEDGER_ACTIVE))

    # Covered leagues first, so opening the file answers "what does this
    # project fetch" without a search through ten thousand rejections. Position
    # carries no meaning — every verdict is read from its own entry — so later
    # hand edits are free to leave a league where it sits.
    entries.sort(key=lambda entry: (entry["verdict"] != LEDGER_ACTIVE,
                                    entry["league_id"]))

    return {
        "_comment": (
            "Every league OpenDota knows about, and what this project decided "
            "about it. Only 'active' leagues are fetched. Editing a verdict "
            "takes effect on the next pipeline run; nothing else needs "
            "changing. Leagues appearing after the seed date arrive as "
            "'pending' and are awaiting a verdict."
        ),
        "source"   : "https://api.opendota.com/api/leagues",
        "seeded_on": seeded_on,
        "leagues"  : entries,
    }


def merge_discovered_leagues(ledger, api_leagues):
    """
    Records leagues OpenDota now lists that the ledger has never seen, as
    `pending`. Returns `(ledger, discovered)` — a new document, and just the
    entries it gained.

    Existing records are copied through untouched, verdict and name alike. A
    rename upstream must not re-case a season of match rows, and it must
    certainly not reopen a decision already taken.

    An empty response discovers nothing. `fetch_url` answers None on failure
    and a list on success, but a truncated list is neither, and treating one as
    news would fill the ledger with duplicates of what it already holds.
    """
    entries = [dict(entry) for entry in ledger_entries(ledger)]
    known   = {entry.get("league_id") for entry in entries}

    discovered = []
    for league in api_leagues or []:
        league_id = league.get("leagueid")
        if league_id is None or league_id in known:
            continue
        known.add(league_id)

        discovered.append(_ledger_entry(
            league_id, league.get("name"), LEDGER_PENDING
        ))

    merged = dict(ledger) if isinstance(ledger, dict) else {}
    merged["leagues"] = entries + discovered

    return merged, discovered


def format_ledger(ledger):
    """
    The ledger as JSON text, one league per line.

    Written by hand rather than by `json.dump(indent=...)`, which would spread
    every league over five lines and turn a 10,000-entry file into a 50,000-line
    one. A verdict change should read as a one-line diff in a pull request,
    because that is where the ledger is reviewed.
    """
    document = dict(ledger) if isinstance(ledger, dict) else {}
    entries  = document.pop("leagues", [])

    lines = []
    for key, value in document.items():
        lines.append(f"  {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)},")

    lines.append('  "leagues": [')
    for position, entry in enumerate(entries):
        comma = "," if position < len(entries) - 1 else ""
        lines.append(f"    {json.dumps(entry, ensure_ascii=False)}{comma}")
    lines.append("  ]")

    return "{\n" + "\n".join(lines) + "\n}\n"


# ── Liquipedia Tier 1 calendar ────────────────────────────────────────────────
#
# What counts as Tier 1 is Liquipedia's Tier 1 Tournaments page, not OpenDota's
# `professional` flag (ADR-0001). The parsing lives here because it takes plain
# HTML and returns plain records; fetching that HTML is `liquipedia.py`.
#
# Only the **rendered tournaments table** is authoritative. The page also
# carries a Timeline template which, as the page itself says, includes Tier 2
# events by the listed organisers. Reading the timeline is what produced the
# false conclusion that FISSURE Universe Episode 8 was Tier 1 and 1win Essence
# II was not — the mistake that left a tournament out of the dataset for two
# months. The row class below is the table, and nothing here looks anywhere else.

# Attribution is a condition of Liquipedia's free API access, not a courtesy.
# The dashboard renders this wherever it shows calendar-derived data (issue 09).
LIQUIPEDIA_ATTRIBUTION = "Tournament calendar from Liquipedia (CC BY-SA 3.0)"

_TIER1_ROW_CLASS        = "table2__row--body"
_TIER1_NAME_CELL_CLASS  = "column__tournament"

_MONTHS = {
    "Jan":  1, "Feb":  2, "Mar":  3, "Apr":  4, "May":  5, "Jun":  6,
    "Jul":  7, "Aug":  8, "Sep":  9, "Oct": 10, "Nov": 11, "Dec": 12,
}
_MONTH_NAMES = "|".join(_MONTHS)

# Four shapes appear on the page. Where only one year is printed it belongs to
# the *end* date:
#   "Nov 28, 2014 - Jul 05, 2015"  both years spelled out — every crossing of a
#                                  new year on the page is written this way
#   "Sep 29 - Oct 11, 2026"        crosses a month inside one year
#   "Oct 19-31, 2027"              inside one month
#   "Feb 10, 2024"                 a single day
_DATE_BOTH_YEARS = re.compile(
    rf"^({_MONTH_NAMES})\s+(\d{{1,2}}),\s*(\d{{4}})"
    rf"\s*-\s*({_MONTH_NAMES})\s+(\d{{1,2}}),\s*(\d{{4}})$"
)
_DATE_CROSS_MONTH = re.compile(
    rf"^({_MONTH_NAMES})\s+(\d{{1,2}})\s*-\s*({_MONTH_NAMES})\s+(\d{{1,2}}),\s*(\d{{4}})$"
)
_DATE_SAME_MONTH = re.compile(
    rf"^({_MONTH_NAMES})\s+(\d{{1,2}})\s*-\s*(\d{{1,2}}),\s*(\d{{4}})$"
)
_DATE_SINGLE_DAY = re.compile(
    rf"^({_MONTH_NAMES})\s+(\d{{1,2}}),\s*(\d{{4}})$"
)

# Liquipedia mixes en dashes, em dashes and hyphens, and separates with regular
# spaces or non-breaking ones. None of that should decide whether a row parses.
_DASHES = str.maketrans({"–": "-", "—": "-", "−": "-", "\xa0": " "})


def _iso(year, month, day):
    """A date as `YYYY-MM-DD`, or None if those numbers are not a real date."""
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_date_range(text):
    """
    Reads one of Liquipedia's date cells into `(start_date, end_date)` as ISO
    strings. A single-day event returns the same date twice rather than a None
    end, so a caller can always treat the pair as a window.

    Returns None when the cell is not a date at all — Liquipedia writes "TBD"
    for events it has not scheduled, and one unscheduled event must not abort
    the whole parse.
    """
    if not text:
        return None

    cleaned = " ".join(str(text).translate(_DASHES).split())

    both_years = _DATE_BOTH_YEARS.match(cleaned)
    if both_years:
        start_month, start_day, start_year, end_month, end_day, end_year = (
            both_years.groups()
        )
        start = _iso(int(start_year), _MONTHS[start_month], int(start_day))
        end   = _iso(int(end_year), _MONTHS[end_month], int(end_day))
        return (start, end) if start and end else None

    crossing = _DATE_CROSS_MONTH.match(cleaned)
    if crossing:
        start_month, start_day, end_month, end_day, year = crossing.groups()
        start_month, end_month = _MONTHS[start_month], _MONTHS[end_month]
        year = int(year)

        # The year is printed once, on the end date, so an event running
        # December to January would start in the *previous* year. Every
        # new-year crossing on the recorded page is written with both years
        # instead and is handled above, so this branch is defensive: it costs
        # one comparison and stops a format change being recorded as an event
        # running backwards by eleven months.
        start_year = year - 1 if start_month > end_month else year

        start = _iso(start_year, start_month, int(start_day))
        end   = _iso(year, end_month, int(end_day))
        return (start, end) if start and end else None

    same_month = _DATE_SAME_MONTH.match(cleaned)
    if same_month:
        month, start_day, end_day, year = same_month.groups()
        start = _iso(int(year), _MONTHS[month], int(start_day))
        end   = _iso(int(year), _MONTHS[month], int(end_day))
        return (start, end) if start and end else None

    single = _DATE_SINGLE_DAY.match(cleaned)
    if single:
        month, day, year = single.groups()
        only = _iso(int(year), _MONTHS[month], int(day))
        return (only, only) if only else None

    return None


def parse_prize_pool(text):
    """
    Reads "$2,744,104" as an integer number of dollars. Returns None for a cell
    that is blank or not a plain dollar figure, so an unusual prize pool costs
    that one field rather than the whole row.
    """
    if not text:
        return None

    match = re.fullmatch(r"\$\s*([\d,]+)", str(text).translate(_DASHES).strip())
    if not match:
        return None

    return int(match.group(1).replace(",", ""))


class _Tier1TableParser(HTMLParser):
    """
    Collects the body rows of the rendered tournaments table.

    Rows are identified by class rather than by position, and the tournament
    name by its own cell class, so the leading icon column can move or vanish
    without silently shifting every field by one.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows        = []
        self._in_row     = False
        self._cells      = []
        self._text       = None
        self._name       = None
        self._name_index = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes    = attributes.get("class", "")

        if tag == "tr":
            self._in_row     = _TIER1_ROW_CLASS in classes
            self._cells      = []
            self._name       = None
            self._name_index = None

        elif tag == "td" and self._in_row:
            self._text = []
            if _TIER1_NAME_CELL_CLASS in classes:
                # `data-sort-value` is the plain name; the cell's own text is
                # the same string wrapped in a link, and occasionally decorated.
                self._name       = attributes.get("data-sort-value")
                self._name_index = len(self._cells)

    def handle_data(self, data):
        if self._text is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if not self._in_row:
            return

        if tag == "td" and self._text is not None:
            self._cells.append(" ".join("".join(self._text).split()))
            self._text = None

        elif tag == "tr":
            self._finish_row()
            self._in_row = False

    def _finish_row(self):
        if self._name_index is None:
            return

        name = self._name or self._cells[self._name_index]
        if not name:
            return

        # Date and prize follow the name cell. Anchoring to the name rather than
        # to column 0 keeps the offsets right if the icon column changes.
        date_cell  = self._get_cell(self._name_index + 1)
        prize_cell = self._get_cell(self._name_index + 2)

        window = parse_date_range(date_cell)
        start, end = window if window else (None, None)

        self.rows.append({
            "name"          : name.strip(),
            "start_date"    : start,
            "end_date"      : end,
            "prize_pool_usd": parse_prize_pool(prize_cell),
        })

    def _get_cell(self, index):
        return self._cells[index] if index < len(self._cells) else None


def parse_tier1_events(html):
    """
    Reads Liquipedia's rendered Tier 1 tournaments table into event records of
    name, start date, end date and prize pool — every year on the page, newest
    first, in the order the page lists them.

    Returns an empty list for HTML that has no such table, rather than raising.
    A page whose markup has changed is a discovery failure, and issue 07 makes
    discovery failures non-fatal: the pipeline carries on from the calendar it
    already has.
    """
    if not html:
        return []

    parser = _Tier1TableParser()
    parser.feed(str(html))
    parser.close()

    return parser.rows


def events_in_year(events, year):
    """
    Selects the events that *begin* in the given year.

    Start date rather than end date, so an event running from December into
    January belongs to the year it opened in — the same year its early matches
    carry in the dataset.
    """
    return [
        event for event in events
        if event.get("start_date") and event["start_date"].startswith(f"{year}-")
    ]


# ── Resolving Tier 1 events to OpenDota leagues ───────────────────────────────
#
# Liquipedia lists *events*; OpenDota lists *leagues*. They are not the same
# object and they never agree on a name — "BLAST SLAM VII" against "Blast Slam
# VII", "PGL Wallachia Season 7" against "PGL Wallachia 2026 Season 7". So names
# are never compared. A league resolves to an event when every match it holds
# falls inside that event's published window, and where a window holds more than
# one such league the one with the highest share of matches involving teams
# already in the dataset wins.
#
# Two consequences worth stating, because both were tested against real data and
# both are why this survives where the obvious approaches do not (ADR-0001):
#
#   - Qualifiers, Division 2 circuits and regional events need no rule of their
#     own. They run outside the main event's window, so the date test drops them
#     without ever reading a name.
#   - The overlap ranking is *relative*. It cannot gate — a qualifier scores
#     higher than the event it qualifies for, because Tier 1 teams play in their
#     own qualifiers — and no absolute threshold would do, since the correct
#     winner scored 85.8% in one window and 74.5% in another.

# Allowed slack at each end of a published window. Never yet needed: OpenDota's
# first and last match dates matched Liquipedia's exactly in all nine played
# 2026 events. It is here for a final that runs past midnight in the venue's
# timezone, not for a case that is known.
RESOLVER_GRACE_DAYS = 2

# How far back resolution reaches. Liquipedia's calendar runs to 2005, and every
# event on it that this project never mapped is, literally, awaiting resolution
# — so without a horizon the first run walks OpenDota's pro match list back
# twenty years, and every run after it walks back to the oldest event it still
# could not resolve. A year covers the dataset's own era and turns the back
# catalogue into a deliberate, separate pass: `tier1-pipeline-automation/14`.
RESOLVER_LOOKBACK_DAYS = 365

# Leagues OpenDota itself says are not competitive events. The tier field cannot
# *select* Tier 1 — every league this project tracks is `professional`, one of
# 2,468 (ADR-0001) — but a streamer showmatch series running inside a Tier 1
# window is a candidate nobody wants to review. This prunes the pool; it never
# picks the winner.
#
# A denylist rather than an allowlist, deliberately. A league with no tier, or
# with a tier this project has never seen, stays a candidate: over-including
# produces an ambiguity that gets reviewed, and under-including produces a
# silent gap, which is the failure the whole feature exists to end.
NON_COMPETITIVE_TIERS = frozenset({"excluded", "amateur"})


def league_catalogue(api_leagues):
    """
    What OpenDota's `/leagues` response says about each league, keyed by id.

    Only the name and the tier: the name so a report can be read, the tier so a
    showmatch series can be kept out of the candidate pool.
    """
    return {
        league["leagueid"]: {"name": league.get("name"), "tier": league.get("tier")}
        for league in api_leagues or []
        if league.get("leagueid") is not None
    }


def summarise_leagues(matches, catalogue=None):
    """
    Reduces a flat list of match rows to one summary per league: the window its
    matches span, how many there are, and every team slot they filled.

    Takes rows in the shape both `/leagues/{id}/matches` and `/proMatches`
    return, which is what lets the shell shortlist from one and confirm from the
    other. A row with no league or no start time is skipped — it cannot be
    placed in a window, and a None reaching the min/max would decide one.

    `team_slots` holds two entries per match, not one per distinct team, because
    the overlap share is a share of *appearances*. An anonymous team keeps its
    slot as None: a team we cannot identify is not an absent one, and a league
    full of them is exactly the league that should score low.
    """
    catalogue = catalogue or {}
    summaries = {}

    for match in matches or []:
        league_id  = match.get("leagueid")
        start_time = match.get("start_time")

        if league_id is None or start_time is None:
            continue

        day = datetime.fromtimestamp(start_time, tz=timezone.utc).strftime("%Y-%m-%d")

        summary = summaries.get(league_id)
        if summary is None:
            listed = catalogue.get(league_id, {})
            summary = summaries[league_id] = {
                "league_id"  : league_id,
                "name"       : listed.get("name"),
                "tier"       : listed.get("tier"),
                "first_date" : day,
                "last_date"  : day,
                "match_count": 0,
                "team_slots" : [],
            }

        summary["first_date"]   = min(summary["first_date"], day)
        summary["last_date"]    = max(summary["last_date"], day)
        summary["match_count"] += 1
        summary["team_slots"].extend(
            [match.get("radiant_team_id"), match.get("dire_team_id")]
        )

    return summaries


def known_team_ids(rows):
    """
    Every team already in the dataset, from its match rows.

    This is what bootstraps the tiebreaker, and it is why the tiebreaker
    degrades for an event with a wholly unfamiliar field. With a small closed
    circuit that is theoretical — and an ambiguous window is surfaced rather
    than resolved silently, so such a case would be visible.
    """
    return {
        team_id
        for row in rows or []
        for team_id in (row.get("radiant_team_id"), row.get("dire_team_id"))
        if team_id is not None
    }


def team_overlap(summary, known_teams):
    """
    The share of a league's team slots held by teams already in the dataset.

    Zero for a league with no matches rather than a division by zero: a league
    listed with nothing played is a real answer from OpenDota, not a fault.
    """
    slots = summary.get("team_slots") or []
    if not slots:
        return 0.0

    return sum(1 for team_id in slots if team_id in known_teams) / len(slots)


def _shift_date(iso_date, days):
    """An ISO date moved by whole days, as an ISO date."""
    return (date.fromisoformat(iso_date) + timedelta(days=days)).isoformat()


def window_coverage(summary, start_date, end_date):
    """
    How much of an event's published window the league's matches actually span,
    as a share from 0 to 1. A league running the full window scores 1.

    This is what separates a tournament from another tournament nested inside
    it. Tier 1 events overlap: FISSURE PLAYGROUND 2 ran from 23 October to 2
    November 2025, entirely inside BLAST Slam IV's 14 October to 9 November —
    so both leagues sit inside BLAST Slam IV's window and both are made
    entirely of teams already tracked, scoring 100% each. Overlap cannot part
    them and match count picks the wrong one, because the nested event happened
    to play more games.

    It ranks; it never gates. A tournament resolves on the day of its first
    match, when it covers one day of a twelve-day window and scores 0.08 — so a
    minimum score here would trade a wrong answer for no answer at all.
    """
    if not start_date or not end_date:
        return 0.0

    opens, closes = date.fromisoformat(start_date), date.fromisoformat(end_date)
    window = (closes - opens).days + 1

    if window <= 0:
        return 0.0

    # Clamped to the window: the grace lets a league begin two days early, and
    # those two days are not more of the event than the event has.
    first = max(date.fromisoformat(summary["first_date"]), opens)
    last  = min(date.fromisoformat(summary["last_date"]), closes)

    return max((last - first).days + 1, 0) / window


def candidates_in_window(summaries, start_date, end_date,
                         grace_days=RESOLVER_GRACE_DAYS):
    """
    The leagues whose matches all fall inside an event's window, by league id.

    *All* of them, not merely some: a league spilling outside the window is a
    different tournament that happens to overlap it. That single condition is
    what excludes qualifiers, Division 2 circuits and regional events without
    naming any of them.

    An event with no published window has no candidates. Liquipedia writes
    "TBD" for an event it has not scheduled, and an unscheduled event must not
    take a candidate at random.
    """
    if not start_date or not end_date:
        return []

    opens  = _shift_date(start_date, -grace_days)
    closes = _shift_date(end_date, grace_days)

    return sorted(
        (
            summary for summary in summaries.values()
            if summary.get("tier") not in NON_COMPETITIVE_TIERS
            and opens <= summary["first_date"] and summary["last_date"] <= closes
        ),
        key=lambda summary: summary["league_id"],
    )


def resolve_tier1_events(events, summaries, known_teams,
                         grace_days=RESOLVER_GRACE_DAYS):
    """
    One resolution per event, in the order the events were given.

    A resolution names the winning league, its overlap score, whether the window
    was contested, and every candidate ranked. An event with no candidate
    resolves to None — a **gap**, which is coverage this project wants and does
    not have, and is recorded rather than raised.

    Candidates rank on team overlap, then on **window coverage**, then on match
    count, then on league id — so a window that scores level resolves the same
    way on every run rather than churning the ledger.

    Coverage is the second key because Tier 1 events overlap each other. FISSURE
    PLAYGROUND 2 ran entirely inside BLAST Slam IV's window in 2025, so both
    leagues are candidates for BLAST Slam IV and both are made of teams already
    tracked, scoring 100% each. Match count alone picks the nested event, which
    played more games in fewer days. Coverage picks the one that ran the window.
    """
    resolutions = []

    for event in events or []:
        start_date, end_date = event.get("start_date"), event.get("end_date")

        # Scored once, here, so a candidate's rank and the numbers reported
        # beside it cannot drift apart.
        candidates = [
            {
                "league_id"  : summary["league_id"],
                "name"       : summary["name"],
                "overlap"    : round(team_overlap(summary, known_teams), 4),
                "coverage"   : round(
                    window_coverage(summary, start_date, end_date), 4
                ),
                "match_count": summary["match_count"],
            }
            for summary in candidates_in_window(
                summaries, start_date, end_date, grace_days
            )
        ]

        candidates.sort(key=lambda candidate: (
            -candidate["overlap"],
            -candidate["coverage"],
            -candidate["match_count"],
            candidate["league_id"],
        ))

        winner = candidates[0] if candidates else None

        resolutions.append({
            "event"      : event.get("name"),
            "start_date" : start_date,
            "end_date"   : end_date,
            "league_id"  : winner["league_id"] if winner else None,
            "league_name": winner["name"] if winner else None,
            "overlap"    : winner["overlap"] if winner else None,
            "ambiguous"  : len(candidates) > 1,
            "candidates" : candidates,
        })

    return resolutions


def apply_resolutions(ledger, resolutions):
    """
    Writes each winning league's Tier 1 event onto its ledger record. Returns
    `(ledger, changes)` — a new document, and the `(league_id, before, after)`
    of every field it moved.

    The **verdict is never touched**. Resolving is discovery; covering a
    tournament is Wade's decision, taken by merging a pull request
    (`tier1-pipeline-automation/15`). A resolver that set `active` itself would
    be fetching leagues nobody approved.

    Nothing is ever cleared. A mapping is only overwritten by a later, positive
    resolution, so a run that walked back a fortnight cannot wipe the answers of
    the run that walked back a year.
    """
    mapped = {
        resolution["league_id"]: resolution["event"]
        for resolution in resolutions or []
        if resolution.get("league_id") is not None
    }

    entries = [dict(entry) for entry in ledger_entries(ledger)]
    changes = []

    for entry in entries:
        event_name = mapped.get(entry.get("league_id"))

        if event_name is None or entry.get("tier1_event") == event_name:
            continue

        changes.append((entry["league_id"], entry.get("tier1_event"), event_name))
        entry["tier1_event"] = event_name

    merged = dict(ledger) if isinstance(ledger, dict) else {}
    merged["leagues"] = entries

    return merged, changes


def resolution_problems(ledger, resolutions):
    """
    The lines a run has to say out loud: a Tier 1 event resolved to a league
    **nobody has judged**.

    That is the whole feature in one sentence. It is the Esports World Cup,
    found by the pipeline rather than by a friend asking why a team is missing.

    Two things are deliberately silent here.

    A `rejected` league is a decision on record, and a decision on record is
    never proposed again — that is what the verdict is *for*, and a run that
    re-raised it every morning would be the noise the ledger exists to end. The
    Tier 1 events this project starts after are exactly this case. They are
    still visible, with their verdict, in `tier1_resolution.json`.

    A gap is not a problem either. It is recorded as a gap, and an unstarted
    tournament printed as a fault every run is how a real one stops being read.
    """
    verdicts = {
        entry.get("league_id"): entry.get("verdict")
        for entry in ledger_entries(ledger)
    }

    settled  = (LEDGER_ACTIVE, LEDGER_REJECTED)
    problems = []
    claimed  = {}

    for resolution in resolutions or []:
        league_id = resolution.get("league_id")

        if league_id is None:
            continue

        # One league cannot be two tournaments. `apply_resolutions` keys its
        # writes by league id, so the second claim would quietly overwrite the
        # first and one of the two events would read as a gap with no reason
        # given. Overlapping Tier 1 windows make this reachable, so it is said
        # out loud rather than resolved by whichever event came last.
        claimed.setdefault(league_id, []).append(resolution["event"])

        if verdicts.get(league_id) not in settled:
            problems.append(
                f"'{resolution['event']}' resolved to league {league_id} "
                f"({resolution.get('league_name')}), whose verdict is "
                f"'{verdicts.get(league_id, 'unrecorded')}' — nobody has "
                f"decided whether to cover it"
            )

    for league_id, events in claimed.items():
        if len(events) > 1:
            problems.append(
                f"league {league_id} was claimed by {len(events)} events "
                f"({', '.join(events)}) — only one of them can be it, and only "
                f"one mapping will survive"
            )

    return problems


def events_awaiting_resolution(events, ledger, today,
                               lookback_days=RESOLVER_LOOKBACK_DAYS):
    """
    The events worth spending API calls on, which is what decides how far back
    the shell walks OpenDota's pro match list.

    Three conditions, and each one is a cost avoided:

    - **Not already mapped.** The ledger's answer does not expire.
    - **Already started.** An event with no matches yet has nothing to find. It
      is a gap, and it stays one until its first match is played — at which
      point it resolves that same day rather than the day after the final.
    - **Inside the lookback.** Liquipedia's list reaches back to 2005 and this
      project has never mapped most of it, so without this the first run walks
      twenty years of pro matches and every run after it walks back to the
      oldest thing it still could not resolve.
    """
    mapped = {
        entry.get("tier1_event") for entry in ledger_entries(ledger)
        if entry.get("tier1_event")
    }

    opened_after = (today - timedelta(days=lookback_days)).isoformat()
    today        = today.isoformat()

    return [
        event for event in events or []
        if event.get("name") not in mapped
        and event.get("start_date")
        and opened_after <= event["start_date"] <= today
    ]


def resolution_walk_start(events, grace_days=RESOLVER_GRACE_DAYS):
    """
    The unix timestamp a pro match walk has to reach to cover these events: the
    earliest window opening, less the same grace the window itself allows.

    The grace matters. A league whose first match is two days early is exactly
    the case the window grace exists for, and a walk stopping on the published
    start date would not reach it.

    Midnight UTC, so a walk is bounded in whole days like the window it serves.
    Whether an event resolves must not depend on what time of day the job ran.

    There is no floor here: `events_awaiting_resolution` has already dropped
    everything outside the lookback, so its earliest event is inside it by
    construction.
    """
    opens = min(date.fromisoformat(event["start_date"]) for event in events)
    opens -= timedelta(days=grace_days)

    return int(datetime(opens.year, opens.month, opens.day,
                        tzinfo=timezone.utc).timestamp())


def tier1_resolution_state(events, ledger, resolutions=None, previous=None):
    """
    One record per Tier 1 event: the league the ledger now maps it to, and the
    candidates that were ranked to get there.

    The mapping comes from the **ledger**, not from `resolutions`. A daily run
    walks back only as far as its unresolved events, so most events are never
    looked at — and an event resolved months ago must not regress to a gap
    because of it.

    `previous` is the last report's records, and it is what keeps the candidates
    alive. An event is examined exactly once — the run after it resolves has
    nothing to do and would otherwise blank the very evidence a contested window
    was recorded for, a day after recording it and before anyone reviewed the
    pull request. So this run's ranking wins where there is one, and the last
    one stands where there is not.

    A record with no `league_id` is a known gap.
    """
    mapped = {}
    for entry in ledger_entries(ledger):
        event_name = entry.get("tier1_event")
        if event_name:
            mapped.setdefault(event_name, entry)

    examined = {
        resolution["event"]: resolution for resolution in resolutions or []
    }
    remembered = {
        record["event"]: record for record in previous or []
    }

    records = []

    for event in events or []:
        name     = event.get("name")
        entry    = mapped.get(name) or {}
        ranking  = examined.get(name) or remembered.get(name) or {}

        records.append({
            "event"      : name,
            "start_date" : event.get("start_date"),
            "end_date"   : event.get("end_date"),
            "league_id"  : entry.get("league_id"),
            "league_name": entry.get("name"),
            "verdict"    : entry.get("verdict"),
            "ambiguous"  : bool(ranking.get("ambiguous")),
            "candidates" : ranking.get("candidates") or [],
        })

    return records


def resolution_report(records, generated_at, grace_days=RESOLVER_GRACE_DAYS):
    """
    The document written to `data/tier1_resolution.json`, which the Upcoming tab
    (`09`) and the approval pull request (`15`) read.

    `generated_at` is passed in because reading the clock is I/O and this
    function does none.
    """
    return {
        "_comment": (
            "Which OpenDota league each Liquipedia Tier 1 event resolved to, by "
            "date window — never by name. A null league_id is a known gap: "
            "coverage this project wants and does not have. 'ambiguous' marks a "
            "window that held more than one candidate, ranked by the share of "
            "matches involving teams already in the dataset. Generated by "
            "opendota_pipeline.py; do not edit by hand."
        ),
        "generated_at"    : generated_at.isoformat(timespec="seconds"),
        "grace_days"      : grace_days,
        "event_count"     : len(records),
        "gap_count"       : sum(1 for record in records
                                if record.get("league_id") is None),
        "ambiguous_count" : sum(1 for record in records
                                if record.get("ambiguous")),
        "attribution"     : LIQUIPEDIA_ATTRIBUTION,
        "events"          : records,
    }
