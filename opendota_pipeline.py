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
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
import pandas as pd

from core import (
    FETCH_COMPLETE,
    FETCH_HELD,
    FETCH_UNPARSED,
    LEDGER_ACTIVE,
    SUSPECT_RETRY_DAYS,
    active_leagues,
    build_rows,
    classify_fetch,
    coverage_meta,
    format_ledger,
    index_by_match_id,
    ledger_problems,
    merge_discovered_leagues,
    seed_ledger,
    store_match,
    suspect_reasons,
    verdict_counts,
)

# %%
# # Step 1 - Configuration and Setup

# Which leagues get fetched is `data/leagues.json`, not a dict here. A dict
# could only say what to fetch; the ledger records a verdict per league, so a
# tournament this project decided against is visibly rejected rather than
# merely absent — the distinction whose absence hid the Esports World Cup for
# two months. See `core.seed_ledger` and ADR-0001.

#File paths
_HERE = Path(__file__).parent
DATA_DIR = str(_HERE / "data")
CHECKPOINT_DIR = str(_HERE / "checkpoints")

MATCHES_FILE = os.path.join(DATA_DIR, "matches.json")
LEDGER_FILE = os.path.join(DATA_DIR, "leagues.json")
MATCH_CHECKPOINT = os.path.join(CHECKPOINT_DIR, "fetched_matches.json")
# Matches given up on: suspect, out of retries, and never to be fetched again.
UNPARSED_CHECKPOINT = os.path.join(CHECKPOINT_DIR, "unparsed_matches.json")
CSV_FILE = os.path.join(DATA_DIR, "matches_flat.csv")
META_FILE = os.path.join(DATA_DIR, "meta.json")

# API Settings
BASE_URL = "https://api.opendota.com/api"
DELAY_SECONDS = 1.0

# Extracted Fields
# Fields to always extract. Every field the flat row is built from has to be
# here: with SAVE_RAW off, this list *is* the payload, and a field missing from
# it reads downstream as missing from the API. Dropping the scores that way
# would make every trimmed match look like a zero-kill game and flag the lot
# as suspect.
CORE_FIELDS = [
    "match_id",
    "duration",
    "patch",
    "radiant_win",
    "radiant_score",
    "dire_score",
    "game_mode",
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
# # Step 2b - The league ledger


def read_ledger():
    """
    The ledger as stored, or None when the file will not parse into one.

    A wrong *shape* counts as unparseable, not as an empty ledger. `{"leagues":
    {}}` reads as no leagues, and treating that as real would let discovery
    append ten thousand pending entries over a file whose verdicts are all
    still sitting there, one syntax error away from being recovered.
    """
    try:
        with open(LEDGER_FILE, "r", encoding="utf-8") as f:
            ledger = json.load(f)
    except (json.JSONDecodeError, OSError) as error:
        print(f"  Could not read the league ledger: {error}")
        return None

    if not isinstance(ledger, dict) or not isinstance(ledger.get("leagues"), list):
        print("  The league ledger is not the expected shape")
        return None

    return ledger


def write_ledger(ledger):
    """Writes the ledger, one league per line, ready to review in a diff."""
    with open(LEDGER_FILE, "w", encoding="utf-8") as f:
        f.write(format_ledger(ledger))


def fetch_all_leagues():
    """
    Every league OpenDota knows about. One call, ~10,000 leagues.

    This is what makes a new tournament discoverable: an id in this list that
    the ledger has never seen is recorded as pending rather than passing
    unnoticed. Returns None on failure, like every other fetch.

    A payload that is not a list counts as a failure. OpenDota can answer 200
    with an error object, and a dict handed on to the ledger iterates as its
    own keys — which is an AttributeError halfway through a run rather than a
    fetch that reported it had failed.
    """
    leagues = fetch_url(f"{BASE_URL}/leagues")

    if leagues is not None and not isinstance(leagues, list):
        print(f"  The league list came back as {type(leagues).__name__}, not a list")
        return None

    return leagues


def load_ledger():
    """
    The ledger for this run, refreshed against OpenDota's league list.

    Four cases, and the ordering matters:

    - **No ledger file.** Seed one from the league list, with nothing active.
      Every league in existence is decided at that moment, so only ids
      appearing afterwards can be pending. Nothing is fetched this run: which
      leagues to cover is a judgement, and a pipeline that guessed would be the
      hardcoded dict again with extra steps.
    - **A ledger that will not parse.** Fetch nothing and write nothing. Every
      verdict in that file is recoverable by fixing the syntax, and none of it
      survives being written over.
    - **Ledger, no league list.** Fetch what it already says. Discovery going
      quiet must never stop match fetching.
    - **Both.** Record anything new as pending, and write the file only if that
      changed something — the file is ten thousand lines and this runs daily.
    """
    if not os.path.exists(LEDGER_FILE):
        api_leagues = fetch_all_leagues()

        # `not`, not `is None` — the opposite of the rule the match loop
        # follows. An empty *match* list is a real answer, from a league that
        # has not started. An empty *league* list is not: OpenDota has ten
        # thousand. Seeding on it would write a ledger holding nothing, and the
        # next run would greet all 10,050 real leagues as newly discovered.
        if not api_leagues:
            print("  No league ledger, and the league list came back empty")
            return {"leagues": []}

        ledger = seed_ledger(api_leagues, seeded_on=date.today().isoformat())
        write_ledger(ledger)
        print(f"  Seeded {LEDGER_FILE} with {len(ledger['leagues'])} leagues, "
              f"all rejected")
        print(f"  Mark the leagues to cover as '{LEDGER_ACTIVE}' and run again")
        return ledger

    ledger = read_ledger()

    if ledger is None:
        print(f"  Fix {LEDGER_FILE} — nothing is fetched or written until it parses")
        return {"leagues": []}

    api_leagues = fetch_all_leagues()

    if api_leagues is None:
        print("  Could not fetch the league list — no discovery this run")
    else:
        merged, discovered = merge_discovered_leagues(ledger, api_leagues)

        if merged != ledger:
            write_ledger(merged)
            ledger = merged

        if discovered:
            print(f"  {len(discovered)} league(s) new since the last run, "
                  f"recorded as pending and awaiting a verdict:")
            for entry in discovered:
                print(f"    {entry['league_id']}  {entry['name']}")

    for problem in ledger_problems(ledger):
        print(f"  Ledger problem: {problem}")

    counts = sorted(verdict_counts(ledger).items(), key=lambda item: str(item[0]))
    print("  Ledger: " + ", ".join(f"{count} {verdict}" for verdict, count in counts))

    return ledger


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


def checkpoint_or_hold(match_data, fetched_matches, unparsed_matches, now):
    """
    Acts on `core.classify_fetch`: checkpoints the match, or deliberately does
    not, and says which so the run can tally it.

    Held matches are the ones **left out** of `fetched_matches`. Nothing else
    schedules the re-fetch — the next run fetches every id it has no record of
    having fetched, so the missing entry is the mechanism.
    """
    match_id = match_data["match_id"]
    verdict  = classify_fetch(match_data, now)

    if verdict == FETCH_HELD:
        print(f"  Match {match_id} looks unparsed "
              f"({';'.join(suspect_reasons(match_data))}) — holding back for re-fetch")
        return verdict

    fetched_matches.add(match_id)

    if verdict == FETCH_UNPARSED:
        # A durable record of what was given up on, so "still zeroed months
        # later" can be told apart from "flagged this morning, may yet fix
        # itself" without re-deriving it from timestamps.
        unparsed_matches.add(match_id)
        print(f"  Match {match_id} still unparsed "
              f"({';'.join(suspect_reasons(match_data))}) after "
              f"{SUSPECT_RETRY_DAYS} days — giving up")

    return verdict


def run_pipeline(leagues):
    """
    Main pipeline function. Loops over the leagues the ledger marks active,
    fetches match IDs, fetches match details, and saves everything to disk.

    `leagues` is `{league_id: name}` — the name is the ledger's and is written
    onto every match row, so it is passed in rather than read back from the API
    per league.
    """
    print("=" * 60)
    print("Starting pipeline...")
    print("=" * 60)

    if not leagues:
        print("The ledger marks no league active — nothing to fetch.")
        print("=" * 60)
        return

    # Load checkpoints and existing data
    fetched_matches   = load_checkpoint(MATCH_CHECKPOINT)
    unparsed_matches  = load_checkpoint(UNPARSED_CHECKPOINT)
    all_matches       = load_existing_matches()
    positions         = index_by_match_id(all_matches)

    # One clock for the run. Retry windows are five days wide, so nothing turns
    # on where inside a run's few minutes a given match is classified.
    now = datetime.now(timezone.utc)

    tally = Counter()

    print(f"Matches already fetched : {len(fetched_matches)}")
    print(f"Matches in file         : {len(all_matches)}")
    print(f"Given up as unparsed    : {len(unparsed_matches)}")
    print()

    # Loop over active leagues
    for league_id, league_name in leagues.items():

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

        # Anything not in the checkpoint: matches never seen, and matches held
        # back by an earlier run because their replay was not parsed yet.
        new_ids = [mid for mid in match_ids if mid not in fetched_matches]

        print(f"  Found {len(match_ids)} total, {len(new_ids)} to fetch")

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

            # Store it, replacing the record of an earlier, unparsed fetch
            store_match(all_matches, positions, match_data)

            # Update match checkpoint — unless the match is being held back
            tally[checkpoint_or_hold(
                match_data, fetched_matches, unparsed_matches, now
            )] += 1
            new_match_count += 1

            # Save every 10 matches
            if new_match_count % 10 == 0:
                save_matches(all_matches)
                save_checkpoint(MATCH_CHECKPOINT, fetched_matches)
                save_checkpoint(UNPARSED_CHECKPOINT, unparsed_matches)
                print(f"  Checkpoint saved — {new_match_count} new matches so far")

        # Save after each league completes
        save_matches(all_matches)
        save_checkpoint(MATCH_CHECKPOINT, fetched_matches)
        save_checkpoint(UNPARSED_CHECKPOINT, unparsed_matches)
        print(f"  Done — {new_match_count} new matches fetched for {league_name}")
        print()

    print("=" * 60)
    print(f"Pipeline complete. Total matches in file: {len(all_matches)}")
    print(f"  Complete            : {tally[FETCH_COMPLETE]}")
    print(f"  Held for re-fetch   : {tally[FETCH_HELD]}")
    print(f"  Permanently unparsed: {tally[FETCH_UNPARSED]} "
          f"(all time: {len(unparsed_matches)})")
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

    run_pipeline(active_leagues(load_ledger()))
    return build_dataframe(patch_map)


if __name__ == "__main__":
    main()
