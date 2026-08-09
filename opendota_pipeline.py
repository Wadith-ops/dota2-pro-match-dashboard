"""
The pipeline shell: everything the pure core deliberately does not do.

Network calls, checkpoint files, the raw sink and the CSV export live here.
The transformations they feed live in `core.py` and are tested against recorded
payloads in `tests/`.

Importing this module does nothing. `main()` runs the pipeline, and only the
`__main__` guard calls it — so `import opendota_pipeline` cannot start a
multi-hour API run, and the tests can import the transforms without one.
"""
# %%
# # Import libraries
import requests
import time
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd

from core import build_rows, coverage_meta

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
    19543: "PGL Wallachia 2026 Season 8",
    19696: "DreamLeague Season 29",
    19101: "Blast Slam VII",
    19785: "Esports World Cup 2026",
    20009: "1win Essence II",
    19719: "The International 2026"
}

# Only fetch the below leagues for this run
ACTIVE_LEAGUES = list(ALL_LEAGUES.keys())

#File paths
_HERE = Path(__file__).parent
DATA_DIR = str(_HERE / "data")
CHECKPOINT_DIR = str(_HERE / "checkpoints")

MATCHES_FILE = os.path.join(DATA_DIR, "matches.json")
MATCH_CHECKPOINT = os.path.join(CHECKPOINT_DIR, "fetched_matches.json")
CSV_FILE = os.path.join(DATA_DIR, "matches_flat.csv")
META_FILE = os.path.join(DATA_DIR, "meta.json")

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


def ensure_directories():
    """
    Creates the data and checkpoint directories. Called from main(), not at
    import — importing this module must not write to disk.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)


# ── Fetch patch mapping from OpenDota constants ───────────────
def get_patch_map():
    """
    Fetches the official patch ID to version name mapping from OpenDota.
    Returns a dictionary like {58: "7.39", 59: "7.40", ...}
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

# %%
# # Step 3a - Checkpoints

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

        # `fetch_url` returns None on failure and a list on success, so an
        # announced-but-not-yet-started league returns []. Testing truthiness
        # would report that empty list as a failed fetch.
        if data is None:
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

# %%
# # Step 5 - Flatten & Export to CSV

def write_meta(rows):
    """
    Writes the coverage record the dashboard reads. The figures themselves are
    computed by core.coverage_meta; this only supplies the clock and the file.
    """
    meta = coverage_meta(rows, datetime.now(timezone.utc))

    with open(META_FILE, "w") as f:
        json.dump(meta, f, indent=2)

    print(f"Meta saved to {META_FILE}")
    for key, value in meta.items():
        print(f"  {key}: {value}")

    return meta


def build_dataframe(patch_map):
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

    rows = build_rows(matches, patch_map)
    df   = pd.DataFrame(rows)

    # ── Convert start_time from unix timestamp to readable date ──
    df["start_time"] = pd.to_datetime(df["start_time"], unit="s")

    # ── Save to CSV ───────────────────────────────────────────
    df.to_csv(CSV_FILE, index=False)

    # ── Record coverage alongside it ──────────────────────────
    # Built from the flat rows, where start_time is still a unix timestamp.
    write_meta(rows)

    print(f"CSV saved to {CSV_FILE}")
    print(f"DataFrame shape: {df.shape[0]} rows x {df.shape[1]} columns")
    print()
    print("Columns:")
    for col in df.columns:
        print(f"  {col}")

    return df

# %%
# # Entry point


def main():
    """
    Fetches everything new, then rebuilds the CSV and the coverage record.
    """
    ensure_directories()

    patch_map = get_patch_map()
    print(f"Patch map loaded: {patch_map}")

    run_pipeline()
    return build_dataframe(patch_map)


if __name__ == "__main__":
    main()
