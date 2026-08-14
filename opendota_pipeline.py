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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import pandas as pd

import liquipedia
from core import (
    CSV_DTYPES,
    FETCH_HELD,
    FETCH_UNPARSED,
    LEDGER_ACTIVE,
    RESOLVER_GRACE_DAYS,
    SUSPECT_RETRY_DAYS,
    UNKNOWN_PATCH,
    active_leagues,
    apply_resolutions,
    build_rows,
    candidates_in_window,
    carry_forward_hotfix,
    checkpoint_from_store,
    classify_fetch,
    coverage_meta,
    events_awaiting_resolution,
    events_in_year,
    extract_standard,
    format_json_lines,
    format_ledger,
    format_run_report,
    format_run_summary,
    iter_json_array,
    keyed_url,
    known_team_ids,
    league_catalogue,
    league_run_record,
    ledger_problems,
    merge_discovered_leagues,
    parse_standard_records,
    patch_releases,
    resolution_due,
    resolution_problems,
    resolution_report,
    resolution_walk_start,
    resolve_tier1_events,
    retry_pause,
    seed_ledger,
    summarise_leagues,
    summarise_run,
    suspect_reasons,
    tier1_resolution_state,
    verdict_counts,
    without_api_key,
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

# The modelling store: one Standard record per match, appended, committed. This
# is what the CSV is built from — the raw sink below is a seam, not a source.
STANDARD_FILE = os.path.join(DATA_DIR, "matches_standard.jsonl")
# The raw sink: full untouched payloads, written only when SAVE_RAW is on, and
# gitignored because it is 288 KB a match. Nothing reads it.
RAW_FILE = os.path.join(DATA_DIR, "matches_raw.jsonl")
# The store both of those replace: a single 1 GB JSON array, rewritten in full
# every ten matches. It is read once, to seed the Standard store, and is deleted
# by hand afterwards — see `tier1-pipeline-automation/11`.
LEGACY_RAW_FILE = os.path.join(DATA_DIR, "matches.json")
LEDGER_FILE = os.path.join(DATA_DIR, "leagues.json")
MATCH_CHECKPOINT = os.path.join(CHECKPOINT_DIR, "fetched_matches.json")
# Matches given up on: suspect, out of retries, and never to be fetched again.
UNPARSED_CHECKPOINT = os.path.join(CHECKPOINT_DIR, "unparsed_matches.json")
# The UTC day the resolver last ran, which is what holds it to one run a day
# while match fetching runs every six hours. Machine state, so it lives in
# `checkpoints/` with the rest of it and is not committed — losing it costs one
# extra walk, which is the direction to fail in. Deleting it is how a run is
# forced, the same idiom as removing a match id to re-fetch a league.
RESOLUTION_CHECKPOINT = os.path.join(CHECKPOINT_DIR, "last_resolution.json")
CSV_FILE = os.path.join(DATA_DIR, "matches_flat.csv")
META_FILE = os.path.join(DATA_DIR, "meta.json")
# Which Tier 1 event each league is, and which events have no league at all.
RESOLUTION_FILE = os.path.join(DATA_DIR, "tier1_resolution.json")

# API Settings
BASE_URL = "https://api.opendota.com/api"

# Valve's own patch list — 117 entries, every lettered revision included, where
# OpenDota's constants carry 61 gameplay patches and no hotfixes at all. It is
# unauthenticated and answers plain JSON. This is the only non-OpenDota endpoint
# the pipeline reaches; it still goes through `fetch_url`, which is where the
# timeout and the retry schedule live.
PATCH_LIST_URL = "https://www.dota2.com/datafeed/patchnoteslist"

# Optional, and absent locally. OpenDota's free tier is keyless and its limit is
# enforced per IP — fine on a workstation, and not on a hosted runner whose
# address is shared and rotates between jobs. A run with no key behaves exactly
# as it always has; `core.keyed_url` decides which URLs carry it, and Valve's
# patch list is not one of them.
OPENDOTA_API_KEY = os.getenv("OPENDOTA_API_KEY", "")

# The free tier allows 60 calls a minute. One second between calls is exactly
# that, which is not under it: the sleep starts after the response, so a run of
# fast responses puts slightly more than sixty inside a rolling minute. The
# extra tenth is the difference between "at the limit" and "inside it".
DELAY_SECONDS = 1.1

# How long a single call may take before it is treated as a failed one. There is
# no default: `requests` waits forever, and on a workstation that is a hung
# terminal somebody eventually notices, while on a six-hourly runner it is a job
# that never ends and a schedule that never runs again. Thirty seconds is
# Liquipedia's timeout too — one number for the project, not one per client.
REQUEST_TIMEOUT_SECONDS = 30.0

# What to do when a call fails is `core.retry_pause`: which failures are worth
# asking about again, how long to wait, and when to stop. It lives in the core
# because it is a decision about a status code rather than an act of I/O, and
# because the whole of it then fits in one tested function — the same shape as
# `classify_fetch`, which holds the whole re-fetch policy.

# The raw sink: a seam, disabled. Every match is fetched in full either way and
# a Standard record is extracted from it; this decides only whether the full
# payload is *also* kept on disk. It defaults off because 288 KB a match cannot
# live in the repository or on a CI runner, and it stays here because starting
# full-fidelity capture for modelling must be a configuration change rather than
# a rewrite. Set SAVE_RAW=true to turn it back on. See ADR-0002.
SAVE_RAW = os.getenv("SAVE_RAW", "false").lower() == "true"

# How many matches are buffered before the stores are appended to. An append is
# cheap, but one write per match is one fsync per match, and the backfill does
# 1,822 of them in a row.
STORE_BATCH = 25


def ensure_directories():
    """
    Creates the data and checkpoint directories. Called from main(), not at
    import — importing this module must not write to disk.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)


# %%
# # Step 1b - Writing files
#
# Two shapes, and which one a file gets is decided by whether it is a log or a
# document. A **document** — the ledger, the coverage record, a checkpoint — has
# to be rewritten whole, so it is written to a neighbouring temporary file and
# renamed over the original: `os.replace` is atomic, so an interrupt leaves the
# previous version intact rather than half of two. A **log** — the Standard
# store, the raw sink — is only ever appended to, so it is opened in append mode
# and never rewritten at all.

def write_text_atomic(path, text):
    """
    Replaces a file's contents, or leaves the file exactly as it was.

    A plain `open(path, "w")` truncates before it writes, so an interrupt
    anywhere in between leaves a file that parses as nothing. That is the window
    the ledger sat in: ten thousand hand-made verdicts, rewritten daily, one
    interrupted write from being unreadable.
    """
    temporary = f"{path}.tmp"

    # No `newline=` argument, deliberately: every file this writes is already
    # committed with the platform's line endings, and pinning them here would
    # turn the next run's diff into a whole-file rewrite of all 10,050 lines.
    with open(temporary, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())

    os.replace(temporary, path)


def append_text(path, text):
    """
    Appends to a file, and returns once the bytes are on the disk rather than in
    the operating system's cache.

    Nothing is read, nothing is rewritten and nothing is held in memory: the
    cost of adding a match is the size of that match, not the size of the store.
    Whole lines are written in one call, so an interrupt can damage at most the
    line being written — which `core.parse_standard_records` reports and skips.
    """
    if not text:
        return

    # `newline="\n"` here, and not on `write_text_atomic`: this file is appended
    # to by whichever machine ran the pipeline, and once that is a Linux runner
    # as well as this one, the platform default would leave one store with two
    # kinds of line ending in it.
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())


# %%
# # Step 1c - The Standard store

def read_standard_store():
    """
    Every Standard record held, in the order the matches were first fetched.

    Read line by line rather than whole: the store is the biggest thing in the
    repository and there is no reason for two copies of it to exist at once.
    """
    if not os.path.exists(STANDARD_FILE):
        return []

    with open(STANDARD_FILE, "r", encoding="utf-8") as f:
        records, damaged = parse_standard_records(f)

    if damaged:
        # Recoverable, and worth saying out loud: the match those lines held is
        # missing from the CSV until it is fetched again.
        print(f"  {damaged} unreadable line(s) in {STANDARD_FILE} — skipped")

    return records


def append_standard(matches):
    """
    Extracts a Standard record from each raw payload and appends it.

    A match fetched twice — every suspect match is, for five days — is appended
    twice, and the second line wins when the store is read. That is the same
    rule the in-memory upsert enforced, moved to the read side, which is what
    lets the write side stay an append.
    """
    append_text(
        STANDARD_FILE,
        format_json_lines(extract_standard(match) for match in matches),
    )


def append_raw(matches):
    """
    Appends the untouched payloads to the raw sink, when the seam is open.

    Deliberately the same line-per-match shape as the Standard store: the sink
    is 14x the size, so the full-rewrite cost this replaces would come back 14x
    worse the day it is switched on.
    """
    if not SAVE_RAW:
        return

    append_text(RAW_FILE, format_json_lines(matches))


def backfill_standard_store():
    """
    Seeds the Standard store from the legacy 1 GB raw file, once.

    Runs only when there is no store and that file is there — so it is a no-op
    on every run after the first, and on any machine that never held the file.
    Without it, the first run after the split would rebuild the CSV from a store
    holding only the matches fetched that morning, which is the dashboard's
    whole dataset gone.

    The file is streamed, one match at a time. `json.load` on it needs the
    document and its parsed form in memory together, which is why it could never
    be processed anywhere but on the machine that wrote it.

    Nothing is deleted here. Reconciling the store against the CSV and removing
    the raw file is `tier1-pipeline-automation/11`, and it is deliberately a
    human's decision.
    """
    if os.path.exists(STANDARD_FILE) or not os.path.exists(LEGACY_RAW_FILE):
        return

    size_gb = os.path.getsize(LEGACY_RAW_FILE) / 1e9
    print(f"Seeding {STANDARD_FILE} from {LEGACY_RAW_FILE} ({size_gb:.2f} GB)...")

    batch = []
    total = 0

    def chunks(handle):
        while True:
            chunk = handle.read(1 << 22)
            if not chunk:
                return
            yield chunk

    with open(LEGACY_RAW_FILE, "r", encoding="utf-8") as f:
        for match in iter_json_array(chunks(f)):
            batch.append(match)

            if len(batch) >= STORE_BATCH:
                append_standard(batch)
                total += len(batch)
                batch = []
                print(f"  {total} matches extracted...")

    append_standard(batch)
    total += len(batch)

    print(f"  {total} Standard records written to {STANDARD_FILE}")
    print(f"  {LEGACY_RAW_FILE} is untouched — reconcile it against the store "
          "before deleting it by hand")


# ── Fetch patch mapping from OpenDota constants ───────────────
def get_patch_map():
    """
    Fetches the official patch ID to version name mapping from OpenDota.
    Returns a dictionary like {58: "7.39", 59: "7.40", ...}
    Falls back to an empty dict if the call fails.

    Through `fetch_url` rather than around it: this was the one HTTP call in the
    project with its own error handling, which meant its own missing timeout and
    its own absent retry. Nothing about the patch constants makes them a
    different kind of request.

    A payload that is not a list counts as a failure, like the league list —
    OpenDota answers 200 with an error object often enough. **So does an empty
    one**, the same rule the league list follows and the opposite of the one
    the match loop follows: a league legitimately has no matches before it
    starts, and there is no such thing as a Dota patch list with nothing in it.

    An empty map is survivable either way: `core.flatten_match` labels every
    unmapped patch "Unknown", so a run without patch names is worse than one
    with them and far better than no run at all.
    """
    patches = fetch_url(f"{BASE_URL}/constants/patch")

    if not isinstance(patches, list) or not patches:
        print("  Could not fetch the patch constants — patch labels will read "
              f"'{UNKNOWN_PATCH}' for anything new")
        return {}

    patch_map = {patch["id"]: patch["name"] for patch in patches
                 if "id" in patch and "name" in patch}

    # Said out loud rather than silently returned short. A partial map labels
    # real patches "Unknown", which reads on the dashboard as a data problem
    # rather than as the fetch that caused it.
    if len(patch_map) != len(patches):
        print(f"  {len(patches) - len(patch_map)} patch constant(s) had no id or "
              f"name and were skipped")

    return patch_map


# ── Fetch the hotfix release table from Valve ─────────────────
def get_patch_releases():
    """
    Fetches Valve's patch list and returns the ascending release table
    `core.resolve_hotfix` reads. An empty table on any failure.

    The datafeed answers `{"patches": [...], "success": true}`, so success is a
    field rather than only a status code — the same shape of trap the MediaWiki
    endpoint sets, and checked the same way. `patches` is checked for being a
    list on top of that, the way `/constants/patch` and `/leagues` are: this is
    the first call `main()` makes, and a payload that raises out of here is a
    day with no matches fetched, no CSV and no resolution. An **empty** list is
    a failure too, by the same rule: there is no such thing as a Dota patch list
    with nothing in it.

    A failure here costs nothing already recorded. `build_dataframe` carries the
    existing column forward, so an unreachable host leaves `patch_hotfix` blank
    on matches fetched today and untouched on the 1,822 that already have one.
    """
    payload = fetch_url(PATCH_LIST_URL)

    if not isinstance(payload, dict) or not payload.get("success"):
        print("  Could not fetch Valve's patch list — no hotfix will be "
              "resolved this run")
        return []

    patches = payload.get("patches")

    if not isinstance(patches, list) or not patches:
        print("  Valve's patch list came back with no patches in it — no "
              "hotfix will be resolved this run")
        return []

    releases = patch_releases(patches)

    # Said out loud rather than silently returned short, the way a partial patch
    # map is. A table missing the newest release resolves every match after it
    # to the one before, which reads as a meta that never moved.
    if len(releases) != len(patches):
        print(f"  {len(patches) - len(releases)} patch release(s) had no name or "
              f"no usable timestamp and were skipped")

    if not releases:
        print("  No usable releases in Valve's patch list — no hotfix will be "
              "resolved this run")

    return releases

# %%
# # Step 2 - API Fetcher

def fetch_url(url, retry_policy=None):
    """
    Makes a GET request to the given URL, retrying what is worth retrying.
    Waits DELAY_SECONDS after every call to respect the rate limit.
    Returns the parsed payload, or None if the call failed for good.

    Four kinds of failure, and only the shell can tell them apart at all:

    - **No response.** A timeout, a dropped connection, a DNS failure. There is
      no status to read a verdict out of, so the policy is asked about `None`
      and answers with the transient schedule.
    - **A status that is not 200.** Handed to the policy as it stands: a 429 or
      a 5xx is asked again, a 404 is not.
    - **A 200 that is not a payload.** A proxy's error page, most often. Not a
      transport failure, so it is not retried — and not a payload either, so it
      is answered None rather than raised out of a function every caller treats
      as returning one or the other.
    - **Anything else at all.** `requests` mostly wraps what it raises, but not
      invariably, and **nothing** may reach the caller: one league's fetch
      raising is one league's fetch ending the whole run. Unlike the three
      above it is not retried — an error nobody anticipated is not evidence
      that asking again will help.

    `attempt` counts failures on this call whatever their kind, so a call that
    fails several ways shares one four-attempt budget. That is deliberate: the
    property worth guaranteeing is that a single call cannot hold the run open,
    not that each kind of failure gets its own allowance.

    `retry_policy` is injected so a caller can be less patient than the default,
    and it resolves at call time rather than as a default argument — the same
    rule `fetch_pro_matches` follows, since a default argument binds once when
    the function is defined.

    The API key is added here and **nowhere is it printed**. Every message below
    reports `url`, the one the caller asked for, rather than `requested`, the one
    with the credential on it — and the text of an exception goes through
    `core.without_api_key`, because `requests` embeds the URL it was given in
    what it raises. The Actions log of a public repository is public, the
    scheduled job uploads its own copy of that log as an artifact, and a secret
    that was never written needs no masking.
    """
    policy    = retry_pause if retry_policy is None else retry_policy
    requested = keyed_url(url, OPENDOTA_API_KEY, BASE_URL)
    attempt   = 0

    while True:
        status = None

        try:
            response = requests.get(requested, timeout=REQUEST_TIMEOUT_SECONDS)
            time.sleep(DELAY_SECONDS)
            status = response.status_code

            if status == 200:
                try:
                    return response.json()
                except ValueError as error:
                    print(f"  Response to {url} was not JSON: {error}")
                    return None

            print(f"  Bad response {status} for URL: {url}")

        except requests.RequestException as error:
            # `status` is still None here, which is what tells the policy that
            # nothing above the network answered at all.
            print(f"  Error fetching {url}: {without_api_key(error)}")

        except Exception as error:
            print(f"  Unexpected error fetching {url}: "
                  f"{without_api_key(repr(error))}")
            return None

        pause = policy(status, attempt)

        if pause is None:
            return None

        print(f"  Retrying {url} in {pause:.0f}s")
        time.sleep(pause)
        attempt += 1

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
    """
    Writes the ledger, one league per line, ready to review in a diff.

    Atomically, because it is 10,050 hand-reviewable verdicts rewritten daily
    and a half-written one parses as no ledger at all — which `load_ledger`
    correctly refuses to fetch or write against, but only after the file it
    needed is already gone.
    """
    write_text_atomic(LEDGER_FILE, format_ledger(ledger))


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


def read_last_resolution():
    """
    The UTC day the resolver last ran, as an ISO string, or None if it never
    has. Anything unreadable is None: a damaged marker means one extra walk,
    where trusting it could mean a resolver that never runs again.

    This is deliberately not read out of `data/tier1_resolution.json`, whose
    `generated_at` is when the answer last *changed* rather than when it was
    last checked — which is the whole point of that file and the wrong question
    here.
    """
    try:
        with open(RESOLUTION_CHECKPOINT, "r", encoding="utf-8") as f:
            marker = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    return marker.get("last_run") if isinstance(marker, dict) else None


def record_resolution_run(day):
    """Records that the resolver ran on `day`, an ISO date string."""
    write_text_atomic(
        RESOLUTION_CHECKPOINT, json.dumps({"last_run": day}) + "\n"
    )


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

    write_text_atomic(
        RESOLUTION_FILE,
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
    )

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

    # Whether this run actually put the question, which is not the same as
    # whether it found anything. `resolve_if_due` records the day only when this
    # is True, so a run that could not ask must not spend the day's one attempt
    # — otherwise a single failed `/leagues` fetch stands down the resolver
    # until tomorrow, and a tournament starting today is recognised a day late.
    asked = True

    if not awaiting:
        print("  Every Tier 1 event under way already maps to a league")

    elif api_leagues is None:
        # Without the league list a showmatch series inside a Tier 1 window
        # cannot be told from the tournament, and every such window would be
        # reported ambiguous. Skipping is the honest answer; guessing is not.
        print("  No league list this run — resolution skipped")
        asked = False

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
        #
        # `is not None`, never `or 0` — the same rule `fetch_pro_matches`
        # follows over the same rows. One row with no start time would make
        # this the epoch, which reads as a walk reaching 1970 and, worse, makes
        # the test below false: the warning would go missing exactly when the
        # walk fell short.
        played  = [match["start_time"] for match in pro_matches
                   if match.get("start_time") is not None]
        reached = min(played) if played else None

        if reached is None:
            # The walk read nothing at all — a refused request, not an empty
            # answer. Nothing can resolve, and the next run should try again
            # rather than treat today as spent.
            print("  Read no pro matches — nothing can resolve this run")
            asked = False
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

    return asked


def resolve_if_due(ledger, api_leagues, known_teams, now):
    """
    Runs the resolver on the first run of each UTC day, and skips it on every
    other run of that day.

    Match fetching is what wants the six-hour cadence introduced in issue 13;
    resolution is not. Its worst case is an event that starts and never
    resolves — no `tier1_event` is ever written, so it stays awaiting for the
    whole 365-day lookback and every run walks `/proMatches` back to its start
    date looking for it again. Measured at 259 pages, one call each: about 7,800
    calls a month daily, and 31,000 six-hourly against a 50,000 budget.

    Gating it costs nothing in outcome. `core.resolution_due` explains why the
    day is the right grain; the marker it reads is machine state, so a runner
    that lost its cache resolves once more than it needed to.

    **The day is recorded only when the resolver could actually put the
    question.** A run that skipped for want of a league list, or whose walk read
    no pro matches, has not spent the day's attempt — recording it there would
    let one failed fetch stand the resolver down until tomorrow, and a
    tournament starting today would be recognised a day late. That is exactly
    what ADR-0009 exists to prevent, and it is the direction `core.resolution_due`
    already fails in.
    """
    today = now.date().isoformat()

    if not resolution_due(read_last_resolution(), today):
        print(f"Tier 1 resolution already ran today ({today}) — skipped")
        return

    if resolve_tier1_leagues(ledger, api_leagues, known_teams, now):
        record_resolution_run(today)
    else:
        print("Tier 1 resolution could not run — the next run will try again")


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

def fetched_checkpoint(now):
    """
    The match ids this run should treat as already fetched.

    The checkpoint file when there is one, and otherwise `core.checkpoint_from_store`
    over the Standard store. `checkpoints/` is machine state and is not
    committed, so on a GitHub Actions runner there is never a file — and a run
    that believed that would re-fetch all 1,822 matches and append every one of
    them to the committed store again, 37 MB at a time.

    The file wins when it exists, because it is the cheaper answer to the same
    question: rebuilding parses the whole store, which this run will do again at
    the end to build the CSV.

    **What is rebuilt is written back**, which is what makes this a recovery
    rather than a workaround. Nothing else would: the checkpoint is saved by
    `run_pipeline`'s `flush()`, and a run that fetches no new matches never
    flushes — so a runner would parse the whole store on every run forever and
    cache a `checkpoints/` directory with no checkpoint in it.
    """
    fetched = load_checkpoint(MATCH_CHECKPOINT)

    if fetched:
        return fetched

    rebuilt = checkpoint_from_store(read_standard_store(), now)

    if rebuilt:
        print(f"No match checkpoint — recovered {len(rebuilt)} fetched "
              f"match ids from the Standard store")
        save_checkpoint(MATCH_CHECKPOINT, rebuilt)

    return rebuilt


def save_checkpoint(filepath, data):
    """
    Saves a set or list to a checkpoint file as JSON.

    Atomically: a truncated checkpoint reads as an empty one, and an empty
    checkpoint means re-fetching every match in the dataset.
    """
    write_text_atomic(filepath, json.dumps(list(data)))

# %%
# # Step 3b - Get Match Details

def get_match_detail(match_id, fetched_matches):
    """
    Fetches full details for a single match.
    Skips the API call if the match ID is already in the fetched_matches checkpoint.
    Returns the full payload, or None if skipped/failed.

    The payload comes back whole whether or not the raw sink is open. What is
    kept from it is `core.extract_standard`'s business, and trimming here as
    well would put the decision in two places — which is how the scores once
    came to be missing from the stored payload while the flat row still read
    them.
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

    return data

# %%
# # Step 4 - Main Loop + Save


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

    Returns one record per league — what it had, what was fetched from it, and
    whether it could be reached at all. That is what the run reports and what
    the scheduled job logs; see `core.league_run_record`.

    **No single league can end the run.** A match-id list that fails is recorded
    as failed and the loop moves to the next league, because the alternative is
    one unreachable tournament costing every other tournament's matches. The
    same goes for a match: it stays out of the checkpoint, so the next run
    fetches it.
    """
    print("=" * 60)
    print("Starting pipeline...")
    print("=" * 60)

    if not leagues:
        print("The ledger marks no league active — nothing to fetch.")
        print("=" * 60)
        return []

    # One clock for the run. Retry windows are five days wide, so nothing turns
    # on where inside a run's few minutes a given match is classified.
    now = datetime.now(timezone.utc)

    # Load checkpoints. The fetched-match checkpoint falls back to the Standard
    # store when there is no file, which is every run on a hosted runner. The
    # unparsed one does not: it is a record of what was given up on rather than
    # a decision, and the decision it would inform — never fetch this again —
    # already lives in the checkpoint above.
    fetched_matches   = fetched_checkpoint(now)
    unparsed_matches  = load_checkpoint(UNPARSED_CHECKPOINT)

    records = []
    pending = []

    def flush():
        """Appends what has been fetched since the last flush."""
        # The stores first, then the checkpoint. A match checkpointed before it
        # is stored is a match nothing will ever fetch again and nothing holds;
        # the other order costs a duplicate line, which the store already
        # resolves on read.
        append_standard(pending)
        append_raw(pending)
        save_checkpoint(MATCH_CHECKPOINT, fetched_matches)
        save_checkpoint(UNPARSED_CHECKPOINT, unparsed_matches)
        pending.clear()

    print(f"Matches already fetched : {len(fetched_matches)}")
    print(f"Given up as unparsed    : {len(unparsed_matches)}")
    print(f"Raw sink                : {'on' if SAVE_RAW else 'off'}")
    print()

    # Loop over active leagues
    for league_id, league_name in leagues.items():

        record = league_run_record(league_id, league_name)
        records.append(record)

        print(f"Processing league: {league_name} ({league_id})")

        # Step 3a — always fetch match ID list to catch new matches
        url = f"{BASE_URL}/leagues/{league_id}/matches"
        print(f"  Fetching match IDs for league {league_id}...")
        data = fetch_url(url)

        # `fetch_url` returns None on failure and a list on success, so an
        # announced-but-not-yet-started league returns []. Testing truthiness
        # would report that empty list as a failed fetch.
        if data is None:
            # Recorded, not merely printed. Nothing is known about this league
            # this run — not even how many matches it has — and a league that
            # keeps failing has to be visible as more than one line lost in a
            # thousand.
            record["list_failed"] = True
            print(f"  Failed to fetch match IDs for league {league_id} — "
                  f"carrying on with the next league")
            print()
            continue

        match_ids = [match["match_id"] for match in data]

        # Anything not in the checkpoint: matches never seen, and matches held
        # back by an earlier run because their replay was not parsed yet.
        new_ids = [mid for mid in match_ids if mid not in fetched_matches]

        record["available"] = len(match_ids)
        record["skipped"]   = len(match_ids) - len(new_ids)

        print(f"  Found {len(match_ids)} total, {len(new_ids)} to fetch")

        if not new_ids:
            print(f"  Nothing new to fetch")
            print()
            continue

        # Step 3b — fetch details for new matches only
        new_match_count = 0

        for match_id in new_ids:

            # Checked here, not read back out of the return value. A match id
            # can appear twice in one list, and `get_match_detail` answers None
            # both for a match it skipped and for one it could not fetch — so
            # counting every None as a failure would report a duplicate id as a
            # broken fetch, in the very line this run is judged by.
            if match_id in fetched_matches:
                record["skipped"] += 1
                continue

            match_data = get_match_detail(match_id, fetched_matches)

            if match_data is None:
                # Out of retries, and deliberately not checkpointed: the next
                # run has no record of having fetched it and will ask again.
                record["failed"] += 1
                continue

            # Add league name for easy reference
            match_data["league_name"] = league_name
            pending.append(match_data)

            # Update match checkpoint — unless the match is being held back.
            # The three verdicts are the record's own count keys, so the tally
            # is the verdict itself rather than a mapping that could drift.
            record[checkpoint_or_hold(
                match_data, fetched_matches, unparsed_matches, now
            )] += 1
            new_match_count += 1

            if len(pending) >= STORE_BATCH:
                flush()
                print(f"  Stored — {new_match_count} new matches so far")

        # Save after each league completes
        flush()
        print(f"  Done — {new_match_count} new matches fetched for {league_name}")
        print()

    print("=" * 60)
    print("Pipeline complete.")
    print(format_run_report(records))
    print()
    # The one figure the table cannot hold: it is checkpoint state rather than
    # anything that happened this run. Everything else is said by the report
    # above and by the summary line `main()` ends with — restating it here in
    # hand-formatted prose is how two accounts of one run come to disagree.
    print(f"  Given up as unparsed, all time: {len(unparsed_matches)}")
    print("=" * 60)

    return records

# %%
# # Step 5 - Flatten & Export to CSV

def write_meta(rows):
    """
    Writes the coverage record the dashboard reads. The figures themselves are
    computed by core.coverage_meta; this only supplies the clock and the file.
    """
    meta = coverage_meta(rows, datetime.now(timezone.utc))

    write_text_atomic(META_FILE, json.dumps(meta, indent=2))

    print(f"Meta saved to {META_FILE}")
    for key, value in meta.items():
        print(f"  {key}: {value}")

    return meta


def read_hotfix_column():
    """
    The `patch_hotfix` already recorded in the CSV, as `{match_id: name}`.

    This is the one place the pipeline reads its own output, and it is read for
    exactly one reason: the CSV is rebuilt whole every run, so a run that could
    not fetch Valve's patch list would otherwise write a blank column over a
    full one. An unreachable host must cost the newest matches their hotfix, not
    every match its hotfix.

    Anything unreadable — no file, no column, a damaged CSV — is an empty
    mapping. Nothing here may stop a run: the column it protects is a nicety
    beside the dataset it is a column of.
    """
    if not os.path.exists(CSV_FILE):
        return {}

    try:
        previous = pd.read_csv(
            CSV_FILE, usecols=["match_id", "patch_hotfix"], dtype=CSV_DTYPES
        )
    except (ValueError, pd.errors.ParserError, UnicodeDecodeError) as problem:
        print(f"  Could not read the existing {CSV_FILE} for its hotfix column "
              f"({problem}) — nothing to carry forward")
        return {}

    recorded = previous.dropna(subset=["patch_hotfix"])

    return dict(zip(recorded["match_id"], recorded["patch_hotfix"]))


def build_dataframe(patch_map, releases=()):
    """
    Flattens every Standard record into a match row, exports the CSV, and
    returns `(DataFrame, rows)`.

    The serving path reads the **Standard store**, not the raw sink. That is the
    whole point of the split: the sink is off by default, so anything that read
    it would see a dataset that stops growing the day it is switched off.
    Standard keeps every field the flat row is built from, which is what makes
    the substitution invisible — see `core.STANDARD_MATCH_FIELDS`.

    The flat rows come back alongside the frame because the resolver's
    tiebreaker is bootstrapped from the teams already in the dataset, and those
    are plain ints in the rows where the frame has re-read them as floats
    alongside NaN. `core.known_team_ids` reads the rows.

    The hotfix column is carried forward from the CSV about to be replaced,
    wherever this run resolved none. Rebuilding whole is what makes the store the
    single source of truth; it is also what would let one unreachable host blank
    a column, and this is the guard against that.
    """
    matches = read_standard_store()

    if not matches:
        print(f"No Standard records in {STANDARD_FILE} — run the pipeline first")
        return None, []

    print(f"Flattening {len(matches)} matches...")

    rows, carried = carry_forward_hotfix(
        build_rows(matches, patch_map, releases), read_hotfix_column()
    )

    if carried:
        print(f"  {carried} hotfix label(s) kept from the previous CSV")

    df = pd.DataFrame(rows)

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
    It also runs **at most once a day**, whatever the cadence of the job around
    it — see `resolve_if_due`.

    The summary line is printed at the very end, after everything that could
    fail has run. That is deliberate: `auto_update.py` records the line, so its
    presence in the output means the run reached the end, and its absence means
    it did not. A run that fetched nothing and a run that fell over are then two
    different entries in the log rather than the same one.
    """
    ensure_directories()

    # Before anything else, and a no-op after the first time: the CSV is built
    # from the Standard store, so the store has to hold the back catalogue
    # before a run can rebuild it.
    backfill_standard_store()

    patch_map = get_patch_map()
    print(f"Patch map loaded: {patch_map}")

    # Two sources, two grains. OpenDota's constants name the gameplay patch a
    # match was played on; Valve's list names the revision within it. The second
    # never overrules the first — see `core.resolve_hotfix` and ADR-0012.
    releases = get_patch_releases()
    print(f"Patch releases loaded: {len(releases)}")

    # Fetched once and handed to both callers. The ledger needs the ids to spot
    # a league it has never seen; the resolver needs the tiers to keep a
    # showmatch series out of a Tier 1 window.
    api_leagues = fetch_all_leagues()
    ledger      = load_ledger(api_leagues)

    records  = run_pipeline(active_leagues(ledger))
    df, rows = build_dataframe(patch_map, releases)

    resolve_if_due(
        ledger, api_leagues, known_team_ids(rows), datetime.now(timezone.utc)
    )

    print(format_run_summary(summarise_run(records)))

    return df


if __name__ == "__main__":
    main()
