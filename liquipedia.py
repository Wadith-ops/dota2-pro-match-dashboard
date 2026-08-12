"""
The Liquipedia client shell: network, cache files, and the clock.

What counts as a Tier 1 tournament is Liquipedia's Tier 1 Tournaments page, not
OpenDota's `professional` flag, which 2,468 leagues carry (ADR-0001). This
module obtains that page; turning it into event records is `core.py`.

Liquipedia sells a commercial API at `api.liquipedia.net`, but grants free
access to the underlying MediaWiki API — a distinct endpoint needing no key —
under four conditions, all of which this module has to honour rather than
merely intend:

    1. a descriptive User-Agent naming the project, with contact details
    2. `action=parse` no more than once per 30 seconds; other calls once per 2
    3. caching, so an unchanged list is not requested again
    4. CC BY-SA 3.0 attribution wherever the data is displayed

Conditions 1-3 live here. Condition 4 is the dashboard's, via
`core.LIQUIPEDIA_ATTRIBUTION`.

See https://liquipedia.net/api-terms-of-use.

Importing this module does nothing — no network, no files, no clock. Every
entry point takes its collaborators as arguments so the tests can run offline.
"""
import json
import sys
import time
from datetime import date
from pathlib import Path

import requests

from core import LIQUIPEDIA_ATTRIBUTION, events_in_year, parse_tier1_events

# ── Terms-mandated settings ───────────────────────────────────────────────────

# Condition 1. Liquipedia states that generic agents such as "python-requests"
# are liable to be blocked, so this is load-bearing rather than polite.
USER_AGENT = (
    "Dota2ProMatchDashboard/1.0 "
    "(https://github.com/Wadith-ops/dota2-pro-match-dashboard; "
    "wade.yang94@gmail.com)"
)

# Condition 2. One parse call per run sits far inside this, and issue 14 — the
# back-catalogue audit that was expected to need several — turned out to need
# one too: the Tier 1 page is a single table covering 2005 to 2027, so any
# period is a slice of one response rather than a call per year. The interval
# therefore protects nothing that currently happens, and it stays because it is
# a term of the free API rather than a rate this project has to be talked into.
PARSE_MIN_INTERVAL_SECONDS = 30.0

# Condition 3. A day, because the Tier 1 list changes roughly 13 times a year —
# refetching it more often than daily returns identical bytes.
CACHE_MAX_AGE_SECONDS = 24 * 60 * 60

# ── Endpoint and files ────────────────────────────────────────────────────────

API_URL    = "https://liquipedia.net/dota2/api.php"
TIER1_PAGE = "Tier_1_Tournaments"

REQUEST_TIMEOUT_SECONDS = 30.0

_HERE = Path(__file__).parent

# The cache is local scratch, like the match checkpoint. The seed calendar is
# committed: it is what the pipeline falls back to on a machine that has never
# reached Liquipedia, and it is the artefact issue 06's ledger absorbs.
CACHE_FILE = _HERE / "checkpoints" / "liquipedia_tier1_cache.json"
SEED_FILE  = _HERE / "data" / "tier1_calendar.json"


class ParseRateLimiter:
    """
    Holds `action=parse` calls to one per `min_interval` seconds.

    The clock and the sleep are injected because both are I/O; the tests drive
    a fake pair rather than spending 30 real seconds proving the limiter waits.
    """

    def __init__(
        self,
        min_interval=PARSE_MIN_INTERVAL_SECONDS,
        monotonic=time.monotonic,
        sleep=time.sleep,
    ):
        self.min_interval = min_interval
        self._monotonic   = monotonic
        self._sleep       = sleep
        self._last_call   = None

    def wait(self):
        """Blocks until the next parse call is allowed, then records it."""
        now = self._monotonic()

        if self._last_call is not None:
            remaining = self.min_interval - (now - self._last_call)
            if remaining > 0:
                self._sleep(remaining)
                now = self._monotonic()

        self._last_call = now


# One limiter for the process, because the interval is only meaningful *between*
# calls. Building a fresh limiter inside `fetch_tier1_html` would give every call
# an empty history and enforce nothing — the rate limit would hold in the tests,
# which inject a shared one, and nowhere else. Constructing it stores two
# callables and a None, so importing this module still does nothing.
_PARSE_LIMITER = ParseRateLimiter()


def fetch_tier1_html(page=TIER1_PAGE, limiter=None, get=None):
    """
    Fetches one page's rendered HTML from the MediaWiki API.

    Returns the HTML, or None on any failure — a bad status, a timeout, a
    MediaWiki-level error, or a response shaped unexpectedly. Discovery going
    quiet must never stop match fetching, so nothing here raises.

    The limiter defaults to the process-wide one, so consecutive calls are 30
    seconds apart however many callers there are.
    """
    limiter = limiter or _PARSE_LIMITER
    get     = get or requests.get

    limiter.wait()

    try:
        response = get(
            API_URL,
            params={
                "action"       : "parse",
                "page"         : page,
                "prop"         : "text",
                "format"       : "json",
                "formatversion": "2",
            },
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except Exception as error:
        print(f"  Liquipedia request failed: {error}")
        return None

    if response.status_code != 200:
        print(f"  Liquipedia returned {response.status_code} for {page}")
        return None

    try:
        payload = response.json()
    except Exception as error:
        print(f"  Liquipedia response was not JSON: {error}")
        return None

    # MediaWiki reports a missing page as HTTP 200 with an `error` key.
    if "error" in payload:
        code = payload["error"].get("code", "unknown")
        print(f"  Liquipedia API error for {page}: {code}")
        return None

    html = payload.get("parse", {}).get("text")
    if not html:
        print(f"  Liquipedia response for {page} carried no rendered text")
        return None

    return html


# Path parameters below default to None and resolve to the module constants at
# call time, never as default arguments. A default argument binds once, when the
# function is defined, so a reassigned CACHE_FILE or SEED_FILE would be honoured
# by whatever prints the path and ignored by whatever writes it — which is how
# `main()` came to report writing a temporary file while overwriting the real
# calendar. Resolving at call time keeps the two the same file.


def load_cache(path=None):
    """The cached calendar, or None when absent or unreadable."""
    path = Path(path or CACHE_FILE)
    if not path.exists():
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except (json.JSONDecodeError, OSError) as error:
        print(f"  Ignoring unreadable Liquipedia cache: {error}")
        return None

    # Valid JSON is not necessarily a cache. A file holding a list or a bare
    # string parses cleanly and then fails on `.get` at the call site, which is
    # a raise out of a function documented never to raise.
    if not isinstance(cache, dict):
        print(f"  Ignoring Liquipedia cache of unexpected shape: {type(cache).__name__}")
        return None

    return cache


def save_cache(events, fetched_at, path=None):
    """Records the calendar and when it was fetched, so condition 3 is met."""
    path = Path(path or CACHE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"fetched_at": fetched_at, "events": events},
            f,
            indent=2,
            ensure_ascii=False,
        )


def load_seed_calendar(path=None):
    """
    The committed calendar the pipeline falls back to.

    This is the artefact option B would have produced by hand. It is generated
    from the same authoritative table rather than transcribed, so it cannot
    disagree with what a successful fetch returns — but its role is option B's:
    a real list to keep working from when Liquipedia is unreachable or its
    markup has moved.
    """
    path = Path(path or SEED_FILE)
    if not path.exists():
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("events", [])
    except (json.JSONDecodeError, OSError, AttributeError) as error:
        print(f"  Ignoring unreadable Tier 1 seed calendar: {error}")
        return []


def cache_is_fresh(cache, now, max_age=CACHE_MAX_AGE_SECONDS):
    """Whether a cached calendar is recent enough to use without refetching."""
    if not cache or "fetched_at" not in cache:
        return False

    try:
        return (now - float(cache["fetched_at"])) < max_age
    except (TypeError, ValueError):
        return False


def get_tier1_events(
    now=None,
    fetch_html=None,
    cache_path=None,
    seed_path=None,
    max_age=CACHE_MAX_AGE_SECONDS,
):
    """
    The Tier 1 event list, and where it came from.

    Returns `{"events": [...], "source": ...}` where source is one of `cache`,
    `network`, `stale-cache` or `seed`. The caller reports the source rather
    than inferring health from an empty list, because "Liquipedia is down" and
    "Liquipedia lists no events" are different facts that must not look alike.

    Never raises and never returns None. A failure degrades one step at a time:
    fresh cache, then network, then whatever cache exists however old, then the
    committed seed calendar.
    """
    now        = time.time() if now is None else now
    fetch_html = fetch_html or fetch_tier1_html
    cache_path = cache_path or CACHE_FILE
    seed_path  = seed_path or SEED_FILE

    cache = load_cache(cache_path)

    # Condition 3: a list fetched today is not fetched again. A fresh cache
    # holding no events is not a reason to skip the fetch — `cache_is_fresh`
    # answers "recent enough", not "usable", and a truncated cache file would
    # otherwise be served as an empty Tier 1 list.
    if cache_is_fresh(cache, now, max_age) and cache.get("events"):
        return {"events": cache["events"], "source": "cache"}

    html = fetch_html()

    if html:
        events = parse_tier1_events(html)
        if events:
            # A calendar we fetched but could not cache is still a calendar.
            # Letting a read-only or full disk propagate here would stop match
            # fetching over a failure to write an optimisation.
            try:
                save_cache(events, now, cache_path)
            except OSError as error:
                print(f"  Could not write the Liquipedia cache: {error}")

            return {"events": events, "source": "network"}

        # 200 with no rows means the markup moved. Falling through to the cache
        # is the difference between a stale calendar and an empty one.
        print("  Liquipedia page carried no tournament rows — markup may have changed")

    if cache and cache.get("events"):
        print("  Falling back to the cached Tier 1 calendar")
        return {"events": cache["events"], "source": "stale-cache"}

    print("  Falling back to the committed Tier 1 seed calendar")
    return {"events": load_seed_calendar(seed_path), "source": "seed"}


def write_seed_calendar(events, recorded_on, path=None):
    """
    Rewrites the committed fallback calendar.

    Kept as a deliberate, separate step rather than a side effect of fetching:
    the fallback is only worth replacing when someone has looked at what
    changed, and a silent rewrite on every run would make it track the same
    failures the live fetch does.
    """
    path = Path(path or SEED_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "_comment": (
                    "Tier 1 tournament calendar, generated from Liquipedia's "
                    "rendered Tier 1 Tournaments table. Fallback used when the "
                    "MediaWiki API is unreachable or its markup has changed. "
                    "Regenerate with `python liquipedia.py --refresh-seed` "
                    "rather than editing by hand."
                ),
                "source"     : "https://liquipedia.net/dota2/Tier_1_Tournaments",
                "license"    : "CC BY-SA 3.0",
                "recorded_on": recorded_on,
                "events"     : events,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
        f.write("\n")


def main(argv=None):
    """
    Prints the Tier 1 calendar, and with `--refresh-seed` rewrites the
    committed fallback from what it just read.
    """
    argv      = sys.argv[1:] if argv is None else argv
    refreshing = "--refresh-seed" in argv

    # Refreshing the fallback means going to Liquipedia, so the day-long cache
    # is bypassed rather than obeyed. Honouring it would make the documented
    # command fail for 24 hours after any ordinary run — which is the normal
    # state of the machine, the daily pipeline having just run.
    result = get_tier1_events(max_age=0) if refreshing else get_tier1_events()

    events = result["events"]
    print(f"Tier 1 calendar: {len(events)} events (source: {result['source']})")

    this_year = events_in_year(events, date.today().year)
    print(f"\n{date.today().year}: {len(this_year)} events")
    for event in this_year:
        prize = event.get("prize_pool_usd")
        prize = f"${prize:,}" if prize else "-"
        print(f"  {event['name']:<34} {event['start_date']} to {event['end_date']}  {prize}")

    if refreshing:
        if result["source"] != "network":
            # Having bypassed the cache, anything but `network` means the fetch
            # itself failed. Writing then would stamp the existing fallback with
            # today's date and make a stale list look freshly verified.
            print(
                f"\nRefusing to refresh the seed: the fetch failed and fell back "
                f"to '{result['source']}', so there is nothing newer to write."
            )
            return result

        write_seed_calendar(events, date.today().isoformat())
        print(f"\nSeed calendar rewritten: {SEED_FILE}")

    print(f"\n{LIQUIPEDIA_ATTRIBUTION}")
    return result


if __name__ == "__main__":
    main()
