"""
The pure transform core.

Every function here takes plain data and returns plain data. No network calls,
no file reads or writes, no checkpointing, no git, no Streamlit — those live in
`opendota_pipeline.py` and `dashboard.py`. That separation is what makes the
pipeline testable: `tests/` exercises this module against recorded payloads and
never goes near the API.

Importing this module has no side effects.
"""
import re
from datetime import datetime, timezone

# The draft format the dataset is overwhelmingly played on. Anything else is
# flagged on the row — never dropped from it.
CAPTAINS_MODE = 2

# The label for a match whose patch the API did not report. Every row carries a
# patch label, so the column is a string throughout and survives the CSV.
UNKNOWN_PATCH = "Unknown"

# How the CSV must be read back. `patch_label` is written as "7.40" and pandas
# would otherwise re-infer it as the float 7.4, losing the trailing zero — the
# thing this column exists to preserve. Passed to `read_csv` by the dashboard.
CSV_DTYPES = {"patch_label": str}

_PATCH_NAME = re.compile(r"^(\d+)\.(\d+)(.*)$")


def patch_sort_key(label):
    """
    Orders patch labels by release.

    Sorting on `float(label)` throws on any name that is not a plain number,
    and OpenDota does hand back lettered revisions such as "7.40b". Those sort
    directly after the patch they revise; anything unparseable — "Unknown"
    included — sorts last rather than raising.
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

    return row


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

    excluded_count is null, not 0, until suspect-match handling lands (see
    tier1-pipeline-automation/05). Null means "not yet computed"; 0 would mean
    "computed, none found" — and five suspect matches are in the dataset today,
    so writing 0 would state something false. The field is present from the
    start so the dashboard does not need changing when 05 fills it in.

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
        "excluded_count"   : None,
        "latest_match_date": latest,
    }
