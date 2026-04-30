
# %%
# # Import libraries
import requests
import time
import json
import os
from pathlib import Path
import pandas as pd

# %%
# # Step 1 - Configuration and Setup

# League definitions
ALL_LEAGUES = {
    17419: "Slam IV",
    18863: "FISSURE PLAYGROUND 2",
    18920: "PGL Wallachia 2025 Season 6",
    17420: "Slam V",
    18988: "DreamLeague Season 27",
    19099: "BLAST Slam VI",
    19269: "DreamLeague Season 28",
    19435: "PGL Wallachia 2026 Season 7",
    19422: "ESL One Birmingham 2026",
    19543: "PGL Wallachia 2026 Season 8"
}

# Only fetch the below leagues for this run
ACTIVE_LEAGUES = list(ALL_LEAGUES.keys()) 

#File paths
_HERE = Path(__file__).parent
DATA_DIR = str(_HERE / "data")
CHECKPOINT_DIR = str(_HERE / "checkpoints")

MATCHES_FILE = os.path.join(DATA_DIR, "matches.json")
MATCH_CHECKPOINT = os.path.join(CHECKPOINT_DIR, "fetched_matches.json")

# API Settings
BASE_URL = "https://api.opendota.com/api"
DELAY_SECONDS = 1.0

# Extracted Fields
# Fields to always extract
CORE_FIELDS = [
    "match_id",
    "duration", 
    "patch",
    "radiant_win",
    "start_time",
    "radiant_team",
    "dire_team",
    "leagueid",
    "objectives",
]

# Set this to True to save the full raw response instead (override with env var SAVE_RAW=false)
SAVE_RAW = os.getenv("SAVE_RAW", "true").lower() == "true"

# Create directories (if not exist)
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(CHECKPOINT_DIR, exist_ok=True)

# ── Fetch patch mapping from OpenDota constants ───────────────
def get_patch_map():
    """
    Fetches the official patch ID to version name mapping from OpenDota.
    Returns a dictionary like {58: "7.38", 59: "7.39", ...}
    Falls back to empty dict if the call fails.
    """
    url = f"{BASE_URL}/constants/patch"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            patches = response.json()
            return {p["id"]: p["name"] for p in patches}
        else:
            print(f"  Could not fetch patch constants: {response.status_code}")
            return {}
    except Exception as e:
        print(f"  Error fetching patch constants: {e}")
        return {}

PATCH_MAP = get_patch_map()
print(f"Patch map loaded: {PATCH_MAP}")

print("Config loaded. Directories ready.")

# %%
# # Step 2 - API Fetcher

def fetch_url(url):
    """
    Makes a GET request to the given URL.
    Waits DELAY_SECONDS after every call to respect the rate limit.
    Returns the response as a Python dictionary, or None if it failed.
    """
    try:
        response = requests.get(url)
        time.sleep(DELAY_SECONDS)

        if response.status_code == 200:
            return response.json()
        else:
            print (f" Bad response {response.status_code} for URL: {url}")
            return None
        
    except Exception as e:
        print(f" Error fetching {url}: {e}")
        return None
    
print ("API Fetcher ready.")

# %%
# # Step 3a - Get League Match IDs

def load_checkpoint(filepath):
    """
    Loads a checkpoint file and returns its contents as a set.
    If the path doesn't exist yet, returns an empty set.
    """
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            return set(json.load(f))
    return set()

def save_checkpoint(filepath, data):
    """
    Saves a set or list to a checkpoint file as JSON.
    """
    with open(filepath, "w") as f:
        json.dump(list(data), f)

def get_league_match_ids(league_id, completed_leagues):
    """
    Fetches all match IDs for a given league.
    Skips the API call if the league is already in the completed_leagues checkpoint.
    Returns a list of match IDs, or empty list if skipped/failed.
    """
    if league_id in completed_leagues:
        print(f"  Skipping league {league_id} — already completed")
        return []
    
    url = f"{BASE_URL}/leagues/{league_id}/matches"
    print(f"  Fetching match IDs for league {league_id}...")
    
    data = fetch_url(url)
    
    if data is None:
        print(f"  Failed to fetch match IDs for league {league_id}")
        return []
    
    match_ids = [match["match_id"] for match in data]
    print(f"  Found {len(match_ids)} matches for league {league_id}")
    return match_ids

print("League match ID fetcher ready.")

# %%
# # Step 3b - Get Match Details

def get_match_detail(match_id, fetched_matches):
    """
    Fetches full details for a single match.
    Skips the API call if the match ID is already in the fetched_matches checkpoint.
    Returns a dictionary of match data, or None if skipped/failed.
    """
    if match_id in fetched_matches:
        print(f"  Skipping match {match_id} — already fetched")
        return None

    url = f"{BASE_URL}/matches/{match_id}"
    print(f"  Fetching match {match_id}...")

    data = fetch_url(url)

    if data is None:
        print(f"  Failed to fetch match {match_id}")
        return None

    if SAVE_RAW:
        return data
    else:
        return {field: data.get(field) for field in CORE_FIELDS}

print("Match detail fetcher ready.")

# %%
# # Step 4 - Main Loop + Save

def load_existing_matches():
    """
    Loads existing matches from the matches file.
    Returns an empty list if the file doesn't exist yet.
    """
    if os.path.exists(MATCHES_FILE):
        with open(MATCHES_FILE, "r") as f:
            return json.load(f)
    return []


def save_matches(matches):
    """
    Saves the full matches list to the matches file.
    Overwrites the file each time with the full updated list.
    """
    with open(MATCHES_FILE, "w") as f:
        json.dump(matches, f, indent=2)


def run_pipeline():
    """
    Main pipeline function. Loops over ACTIVE_LEAGUES, fetches match IDs,
    fetches match details, and saves everything to disk.
    """
    print("=" * 60)
    print("Starting pipeline...")
    print("=" * 60)

    # Load checkpoints and existing data
    fetched_matches = load_checkpoint(MATCH_CHECKPOINT)
    all_matches     = load_existing_matches()

    print(f"Matches already fetched : {len(fetched_matches)}")
    print(f"Matches in file         : {len(all_matches)}")
    print()

    # Loop over active leagues
    for league_id in ACTIVE_LEAGUES:

        league_name = ALL_LEAGUES.get(league_id, "Unknown League")
        print(f"Processing league: {league_name} ({league_id})")

        # Step 3a — always fetch match ID list to catch new matches
        url = f"{BASE_URL}/leagues/{league_id}/matches"
        print(f"  Fetching match IDs for league {league_id}...")
        data = fetch_url(url)

        if not data:
            print(f"  Failed to fetch match IDs for league {league_id}")
            print()
            continue

        match_ids = [match["match_id"] for match in data]
        new_ids   = [mid for mid in match_ids if mid not in fetched_matches]

        print(f"  Found {len(match_ids)} total, {len(new_ids)} new matches")

        if not new_ids:
            print(f"  Nothing new to fetch")
            print()
            continue

        # Step 3b — fetch details for new matches only
        new_match_count = 0
        for match_id in new_ids:

            match_data = get_match_detail(match_id, fetched_matches)

            if match_data is None:
                continue

            # Add league name for easy reference
            match_data["league_name"] = league_name

            # Append to in-memory list
            all_matches.append(match_data)

            # Update match checkpoint
            fetched_matches.add(match_id)
            new_match_count += 1

            # Save every 10 matches
            if new_match_count % 10 == 0:
                save_matches(all_matches)
                save_checkpoint(MATCH_CHECKPOINT, fetched_matches)
                print(f"  Checkpoint saved — {new_match_count} new matches so far")

        # Save after each league completes
        save_matches(all_matches)
        save_checkpoint(MATCH_CHECKPOINT, fetched_matches)
        print(f"  Done — {new_match_count} new matches fetched for {league_name}")
        print()

    print("=" * 60)
    print(f"Pipeline complete. Total matches in file: {len(all_matches)}")
    print("=" * 60)


# ── Run the pipeline ──────────────────────────────────────────
run_pipeline()

# %%
# # Step 5 - Flatten & Export to CSV

CSV_FILE = os.path.join(DATA_DIR, "matches_flat.csv")

def flatten_objectives(objectives):
    """
    Extracts and counts all objective types from the objectives array.
    Returns a dictionary of counts and timings.
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


def flatten_match(match):
    """
    Flattens a single raw match dictionary into a flat row for the DataFrame.
    """
    # ── Patch mapping ─────────────────────────────────────────
    raw_patch = match.get("patch")
    patch     = PATCH_MAP.get(raw_patch, str(raw_patch)) if raw_patch else None

    # ── Duration conversions ──────────────────────────────────
    duration_secs = match.get("duration")
    duration_mins = round(duration_secs / 60, 1) if duration_secs else None

    # ── Flat fields ───────────────────────────────────────────
    row = {
        "match_id"          : match.get("match_id"),
        "league_id"         : match.get("leagueid"),
        "league_name"       : match.get("league_name"),
        "patch"             : patch,
        "start_time"        : match.get("start_time"),
        "duration_secs"     : duration_secs,
        "duration_mins"     : duration_mins,
        "radiant_win"       : match.get("radiant_win"),
        "radiant_score"     : match.get("radiant_score"),
        "dire_score"        : match.get("dire_score"),
        "game_mode"         : match.get("game_mode"),
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
    for time_field in ["first_roshan_time", "first_blood_time"]:
        raw_time = obj_data.get(time_field)
        obj_data[f"{time_field}_mins"] = round(raw_time / 60, 1) if raw_time else None

    row.update(obj_data)

    return row


def build_dataframe():
    """
    Loads matches.json, flattens every match, and returns a pandas DataFrame.
    Also saves to CSV.
    """
    if not os.path.exists(MATCHES_FILE):
        print("No matches file found — run the pipeline first")
        return None

    with open(MATCHES_FILE, "r") as f:
        matches = json.load(f)

    if not matches:
        print("No matches found")
        return None

    print(f"Flattening {len(matches)} matches...")

    rows = [flatten_match(match) for match in matches]
    df   = pd.DataFrame(rows)

    # ── Convert start_time from unix timestamp to readable date ──
    df["start_time"] = pd.to_datetime(df["start_time"], unit="s")

    # ── Save to CSV ───────────────────────────────────────────
    df.to_csv(CSV_FILE, index=False)

    print(f"CSV saved to {CSV_FILE}")
    print(f"DataFrame shape: {df.shape[0]} rows x {df.shape[1]} columns")
    print()
    print("Columns:")
    for col in df.columns:
        print(f"  {col}")

    return df


df = build_dataframe()
# %%
