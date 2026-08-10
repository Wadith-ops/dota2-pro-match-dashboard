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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import pandas as pd

import liquipedia
from core import (
    FETCH_COMPLETE,
    FETCH_HELD,
    FETCH_UNPARSED,
    LEDGER_ACTIVE,
    RESOLVER_GRACE_DAYS,
    SUSPECT_RETRY_DAYS,
    active_leagues,
    apply_resolutions,
    build_rows,
    candidates_in_window,
    classify_fetch,
    coverage_meta,
    events_awaiting_resolution,
    events_in_year,
    format_ledger,
    index_by_match_id,
    known_team_ids,
    league_catalogue,
    ledger_problems,
    merge_discovered_leagues,
    resolution_problems,
    resolution_report,
    resolution_walk_start,
    resolve_tier1_events,
    seed_ledger,
    store_match,
    summarise_leagues,
    suspect_reasons,
    tier1_resolution_state,
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
# Which Tier 1 event each league is, and which events have no league at all.
RESOLUTION_FILE = os.path.join(DATA_DIR, "tier1_resolution.json")

# API Settings
BASE_URL = "https://api.opendota.com/api"

# The free tier allows 60 calls a minute. One second between calls is exactly
# that, which is not under it: the sleep starts after the response, so a run of
# fast responses puts slightly more than sixty inside a rolling minute. The
# extra tenth is the difference between "at the limit" and "inside it".
DELAY_SECONDS = 1.1

# What to do when the limit is hit anyway. A 429 is not a 404 — the call will
# succeed shortly, and treating it like a missing page throws away whatever it
# was fetching. The resolver is what made this matter: its pro match walk is a
# hundred-odd consecutive calls, and the first run of it was refused on every
# request that followed. Backing off is deliberately dumb; making the whole
# fetcher survive network failures is `tier1-pipeline-automation/12`.
RATE_LIMIT_STATUS   = 429
RATE_LIMIT_BACKOFFS = (30.0, 60.0, 120.0)

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

def fetch_url(url, backoffs=None):
    """
    Makes a GET request to the given URL.
    Waits DELAY_SECONDS after every call to respect the rate limit.
    Returns the response as a Python dictionary, or None if it failed.

    A 429 is retried after a pause rather than reported as a failure, because
    it is the one status that says "ask again". Every other non-200 is answered
    None, as before. Retries are exhausted quietly: a caller that has been
    refused four times over three and a half minutes has genuinely failed.
    """
    backoffs = RATE_LIMIT_BACKOFFS if backoffs is None else backoffs

    for pause in (*backoffs, None):
        try:
            response = requests.get(url)
            time.sleep(DELAY_SECONDS)
        except Exception as e:
            print(f" Error fetching {url}: {e}")
            return None

        if response.status_code == 200:
            return response.json()

        if response.status_code != RATE_LIMIT_STATUS or pause is None:
            print(f" Bad response {response.status_code} for URL: {url}")
            return None

        print(f" Rate limited — waiting {pause:.0f}s before retrying {url}")
        time.sleep(pause)

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


# What `load_ledger` uses to tell "the caller did not pass a league list" from
# "the caller passed None because the fetch failed". The two must not collapse:
# one means fetch it, the other means discovery is off this run.
_FETCH_LEAGUES = object()


def load_ledger(api_leagues=_FETCH_LEAGUES):
    """
    The ledger for this run, refreshed against OpenDota's league list.

    `api_leagues` is the `/leagues` response. It is a parameter because the
    resolver needs the same response — for the tier that keeps a showmatch
    series out of a Tier 1 window — and fetching ten thousand leagues twice a
    run to hand the same list to two callers is a call spent on nothing. Left
    out, it is fetched here as before.

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
    if api_leagues is _FETCH_LEAGUES:
        api_leagues = fetch_all_leagues()

    if not os.path.exists(LEDGER_FILE):
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
# # Step 2c - Resolving Tier 1 events to leagues
#
# The join between Liquipedia's list of events and OpenDota's list of leagues,
# which is the one thing the ledger could not do for itself: it records coverage
# decisions but does not *make* them, so a `pending` entry sat waiting for a
# human to recognise a tournament by eye. The ranking and the window arithmetic
# are `core.py`; this is the network and the files. See ADR-0001 and ADR-0009.

# OpenDota returns 100 pro matches a page. The page cap is a stop on a walk that
# would otherwise follow a pathological `since` back through years of matches —
# 400 pages is 40,000 matches, comfortably more than a year of them.
PRO_MATCHES_MAX_PAGES = 400

# How often the walk says where it has got to. The first run is a backfill of a
# few hundred pages, and several minutes of silence reads like a hung job.
PRO_MATCHES_PROGRESS_EVERY = 25


def fetch_pro_matches(since, max_pages=None):
    """
    Every professional match played since `since`, a unix timestamp, walking
    OpenDota's `/proMatches` pages backwards from now.

    The page that crosses the cutoff is kept whole. A page is a hundred matches
    and the walk has to read one to know it has gone far enough, so trimming it
    would discard matches already paid for.

    Returns what it managed to read. A short walk costs a *missed* candidate,
    never a wrong one: every shortlisted league is re-read in full before it is
    allowed to win a window, so a league whose matches began before the walk did
    is dropped by the second pass rather than believed.

    `max_pages` resolves to the module constant at call time rather than as a
    default argument, which would bind once when the function was defined and
    leave a reassigned cap honoured nowhere.
    """
    max_pages  = PRO_MATCHES_MAX_PAGES if max_pages is None else max_pages
    collected  = []
    older_than = None

    for page_number in range(1, max_pages + 1):
        url = f"{BASE_URL}/proMatches"
        if older_than is not None:
            url += f"?less_than_match_id={older_than}"

        page = fetch_url(url)

        if page is None:
            print(f"  Pro match walk stopped early after {len(collected)} matches")
            break

        # `not page`, and it means the end of the list rather than a failure —
        # there is always a last page, and OpenDota answers it with [].
        if not page:
            break

        collected.extend(page)

        # `is not None`, never truthiness, and never `or 0`. A single match with
        # no start_time would read as the epoch, look older than any cutoff, and
        # end the walk one page in — the whole backfill lost to one thin row.
        # An id-less page is different: without an id there is no way to ask for
        # the page after it, so the walk genuinely cannot continue.
        start_times = [match["start_time"] for match in page
                       if match.get("start_time") is not None]
        match_ids   = [match["match_id"] for match in page
                       if match.get("match_id") is not None]

        # A backfill is a few hundred pages and several minutes of silence
        # otherwise, which reads exactly like a hung run.
        if page_number % PRO_MATCHES_PROGRESS_EVERY == 0 and start_times:
            print(f"    ...{len(collected)} pro matches, back to "
                  f"{datetime.fromtimestamp(min(start_times), tz=timezone.utc):%Y-%m-%d}")

        if start_times and min(start_times) < since:
            break

        if not match_ids:
            print("  Pro match page carried no match ids — the walk cannot go on")
            break

        older_than = min(match_ids)

    return collected


def read_resolution():
    """The resolution as last written, or None when there is none to read."""
    try:
        with open(RESOLUTION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def write_resolution(records, generated_at, existing=None):
    """
    Records which league each Tier 1 event resolved to, and which events have no
    league at all.

    Written **only when the answer changes**. The file is committed and pushed,
    and a run that re-derived the same mapping would otherwise put a commit in
    front of Wade every morning saying nothing but the time of day. So
    `generated_at` is when this answer was reached, not when it was last
    confirmed — which is the more useful of the two anyway.

    `existing` is the report already on disk. The caller has read it, because
    building `records` needs it too, and reading a file twice in one function
    call to answer the same question is how the two answers come to differ.
    """
    report = resolution_report(records, generated_at)

    if existing and existing.get("events") == report["events"]:
        print("  Tier 1 resolution unchanged")
        return existing

    with open(RESOLUTION_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"  Tier 1 resolution saved to {RESOLUTION_FILE}: "
          f"{report['event_count']} events, {report['gap_count']} gap(s), "
          f"{report['ambiguous_count']} ambiguous window(s)")

    return report


def confirm_candidates(shortlist, catalogue):
    """
    Re-reads each shortlisted league's full match list, so its window is the
    whole tournament rather than the part of it the pro match walk reached.

    A league that cannot be re-read is dropped rather than trusted: a truncated
    window is narrower than the real one, which is the direction that makes a
    league *look* like it fits inside an event it has no business winning.
    """
    rows = []

    for league_id in shortlist:
        matches = fetch_url(f"{BASE_URL}/leagues/{league_id}/matches")

        if matches is None:
            print(f"  Could not re-read league {league_id} — dropped from the "
                  f"candidates rather than judged on a partial window")
            continue

        rows.extend(matches)

    return summarise_leagues(rows, catalogue)


def resolve_tier1_leagues(ledger, api_leagues, known_teams, now):
    """
    Resolves Liquipedia's Tier 1 events to OpenDota leagues by date window, and
    records the answer in both places it belongs: `tier1_event` on the ledger
    record, and `data/tier1_resolution.json` for the dashboard and the approval
    pull request. Both are written here; nothing is returned.

    Only events that have **started, are not already mapped, and began within
    the lookback** cost anything. In steady state that is none of them, and the
    whole step is one Liquipedia cache read and no OpenDota calls at all.
    """
    print("=" * 60)
    print("Resolving Tier 1 events to leagues...")

    calendar = liquipedia.get_tier1_events()
    events   = calendar["events"]
    previous = read_resolution()

    # The source, not the length. An empty list is a failed fetch, never a
    # Tier 1 calendar with nothing on it — see `liquipedia.get_tier1_events`.
    print(f"  Tier 1 calendar: {len(events)} events (source: {calendar['source']})")

    awaiting    = events_awaiting_resolution(events, ledger, now.date())
    resolutions = []

    if not awaiting:
        print("  Every Tier 1 event under way already maps to a league")

    elif api_leagues is None:
        # Without the league list a showmatch series inside a Tier 1 window
        # cannot be told from the tournament, and every such window would be
        # reported ambiguous. Skipping is the honest answer; guessing is not.
        print("  No league list this run — resolution skipped")

    else:
        print(f"  {len(awaiting)} event(s) awaiting resolution: "
              + ", ".join(event["name"] for event in awaiting))

        catalogue = league_catalogue(api_leagues)
        since     = resolution_walk_start(awaiting)

        pro_matches = fetch_pro_matches(since)

        # The date the walk *reached*, never the date it was aiming at. A walk
        # cut short by the page cap or a refused request still has to say so:
        # the events it did not reach resolve to nothing, and a gap that is
        # really "we ran out of pages" must not read like a gap that is really
        # "OpenDota has no data".
        reached = min((match.get("start_time") or 0) for match in pro_matches
                      ) if pro_matches else None

        if reached is None:
            print("  Read no pro matches — nothing can resolve this run")
        else:
            print(f"  Read {len(pro_matches)} pro matches back to "
                  f"{datetime.fromtimestamp(reached, tz=timezone.utc):%Y-%m-%d}")

            if reached > since:
                print(f"  The walk stopped short of "
                      f"{datetime.fromtimestamp(since, tz=timezone.utc):%Y-%m-%d}"
                      f" — events before it cannot resolve this run")

        # Windows built from the walk alone, which is why nothing may win on
        # them: a league that began before the walk did looks narrower here
        # than it is. `confirm_candidates` re-reads each one in full.
        unconfirmed = summarise_leagues(pro_matches, catalogue)
        shortlist   = sorted({
            summary["league_id"]
            for event in awaiting
            for summary in candidates_in_window(
                unconfirmed, event.get("start_date"), event.get("end_date")
            )
        })
        print(f"  {len(shortlist)} league(s) fall inside a Tier 1 window")

        resolutions = resolve_tier1_events(
            awaiting, confirm_candidates(shortlist, catalogue), known_teams
        )

        ledger, changes = apply_resolutions(ledger, resolutions)

        if changes:
            write_ledger(ledger)
            for league_id, _, event_name in changes:
                print(f"  League {league_id} resolved to '{event_name}'")

        # A contested window is surfaced in the run, not only in the file. The
        # winner may be right and usually is, but "resolved silently" is the
        # thing this feature was written to stop, and a line nobody printed is
        # as silent as a decision nobody recorded.
        for resolution in resolutions:
            if not resolution["ambiguous"]:
                continue
            print(f"  '{resolution['event']}' had "
                  f"{len(resolution['candidates'])} candidates:")
            for position, candidate in enumerate(resolution["candidates"]):
                marker = "won " if position == 0 else "    "
                print(f"    {marker} {candidate['league_id']:>6}  "
                      f"{candidate['overlap'] * 100:5.1f}% teams  "
                      f"{candidate['coverage'] * 100:5.1f}% window  "
                      f"{candidate['match_count']:>4}m  {candidate['name']}")

        for problem in resolution_problems(ledger, resolutions):
            print(f"  ATTENTION: {problem}")

    # The report looks forward — this year and next — whether or not this run
    # examined any of it. What it is for is the events with no league, and every
    # one of those is still to come. An older event this run happened to resolve
    # is recorded where it belongs, on its ledger entry.
    horizon = sorted(
        events_in_year(events, now.year) + events_in_year(events, now.year + 1),
        key=lambda event: event["start_date"],
        reverse=True,
    )
    records = tier1_resolution_state(
        horizon, ledger, resolutions, (previous or {}).get("events")
    )

    for record in records:
        if record["league_id"] is None:
            print(f"  Gap: {record['event']} ({record['start_date']} to "
                  f"{record['end_date']}) has no OpenDota league")

    write_resolution(records, now, previous)
    print("=" * 60)


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
    Loads matches.json, flattens every match, and returns `(DataFrame, rows)`.
    Also saves to CSV.

    The flat rows come back alongside the frame because the resolver's
    tiebreaker is bootstrapped from the teams already in the dataset, and those
    are plain ints in the rows where the frame has re-read them as floats
    alongside NaN. `core.known_team_ids` reads the rows.
    """
    if not os.path.exists(MATCHES_FILE):
        print("No matches file found — run the pipeline first")
        return None, []

    with open(MATCHES_FILE, "r") as f:
        matches = json.load(f)

    if not matches:
        print("No matches found")
        return None, []

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

    return df, rows

# %%
# # Entry point


def main():
    """
    Fetches everything new, rebuilds the CSV and the coverage record, then
    resolves Liquipedia's Tier 1 events to the leagues that played them.

    Resolution runs **last** because its tiebreaker reads the teams already in
    the dataset, and this run may have just added some. It informs the next
    run rather than this one, which is the right way round: a league it has
    newly recognised still needs a verdict before anything is fetched from it.
    """
    ensure_directories()

    patch_map = get_patch_map()
    print(f"Patch map loaded: {patch_map}")

    # Fetched once and handed to both callers. The ledger needs the ids to spot
    # a league it has never seen; the resolver needs the tiers to keep a
    # showmatch series out of a Tier 1 window.
    api_leagues = fetch_all_leagues()
    ledger      = load_ledger(api_leagues)

    run_pipeline(active_leagues(ledger))
    df, rows = build_dataframe(patch_map)

    resolve_tier1_leagues(
        ledger, api_leagues, known_team_ids(rows), datetime.now(timezone.utc)
    )

    return df


if __name__ == "__main__":
    main()
