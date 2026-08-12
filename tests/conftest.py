"""
Shared fixtures: recorded raw OpenDota match payloads, plus one recorded
Liquipedia page.

Reading these gzipped files is the only filesystem access the suite performs.
Nothing here touches the network, so the suite runs offline.

Each match payload is a match exactly as `opendota_pipeline` stored it, which
means it carries the `league_name` the pipeline injects on top of the API
response.
"""
import gzip
import json
from pathlib import Path

import pytest

from core import patch_releases

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_payload(name):
    """Reads one recorded match payload."""
    with gzip.open(FIXTURE_DIR / f"{name}.json.gz", "rt", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def normal_match():
    """8920872222 — 1win Essence II. Captain's Mode, complete objectives."""
    return load_payload("normal")


@pytest.fixture
def empty_objectives_match():
    """8809802771 — DreamLeague S29. A 52-minute game with no objectives at all."""
    return load_payload("empty_objectives")


@pytest.fixture
def negative_first_blood_match():
    """8549890849 — Slam IV. First blood at -37s, a valid pre-horn kill."""
    return load_payload("negative_first_blood")


@pytest.fixture
def game_mode_1_match():
    """8514293820 — Slam IV. Game mode 1 (All Pick), not Captain's Mode."""
    return load_payload("game_mode_1")


@pytest.fixture
def suspect_matches():
    """
    All five known suspect matches — DreamLeague S29, 8809802771, 8809797141,
    8809726019, 8822088303 and 8821954344 — in the order the issue lists them.

    Every one omits `objectives` entirely while carrying a full duration and a
    real hero kill score, which is what makes them indistinguishable from a
    match where nothing happened. 8821954344 is the 96-minute game.
    """
    return load_payload("suspect_objectives")


@pytest.fixture
def tier1_resolution():
    """
    `data/tier1_resolution.json` as the resolver wrote it on 2026-08-10: 16
    events, 7 of them gaps, 3 contested windows.

    A recorded copy rather than a read of the live file. The live one moves
    every time a tournament resolves — The International resolves the day it
    starts — and a test asserting that it is still a gap would then be asserting
    that the pipeline had stopped working.
    """
    return load_payload("tier1_resolution")


@pytest.fixture
def patch_map():
    """The subset of the OpenDota patch constants the fixtures need."""
    return {58: "7.39", 59: "7.40", 60: "7.41"}


@pytest.fixture
def valve_patch_list():
    """
    Valve's whole patch list as `dota2.com/datafeed/patchnoteslist` answered it
    on 2026-08-12: 117 entries from 7.08 to 7.41e, every lettered revision
    included, in the `{"patches": [...], "success": true}` envelope it arrives
    in — kept whole so the shell's unwrapping is exercised by the same fixture
    as the resolution.

    This is the only source for hotfix names. OpenDota's constants stop at the
    gameplay patch.
    """
    return load_payload("valve_patch_list")


@pytest.fixture
def patch_release_table(valve_patch_list):
    """The same list as the ascending `[(timestamp, name)]` table."""
    return patch_releases(valve_patch_list["patches"])


@pytest.fixture
def hotfix_boundary_matches():
    """
    Every match in the dataset the two patch sources disagree about: 28 games of
    DreamLeague Season 27, played on 2025-12-15 between Valve's timestamp for
    7.40 and OpenDota's. Recorded 2026-08-12 at 1,822 matches.

    Four fields per match, because the disagreement turns on two of them.
    """
    return load_payload("hotfix_boundary")


# The two page-sized fixtures below are session-scoped: the Liquipedia envelope
# is 61 KB of HTML and the 2026 league pool is 29,000 match rows, and reloading
# either for every test that reads it put four seconds on a suite that runs in
# one. Nothing mutates them — they are recorded payloads, read and parsed.
@pytest.fixture(scope="session")
def liquipedia_response():
    """
    The full MediaWiki `action=parse` envelope for Tier 1 Tournaments, recorded
    2026-08-09. Kept whole rather than trimmed to the table, so the client's
    unwrapping of `parse.text` is exercised by the same fixture as the parser.
    """
    return load_payload("liquipedia_tier1")


@pytest.fixture(scope="session")
def liquipedia_tier1_html(liquipedia_response):
    """Just the rendered HTML, which is what the pure parser takes."""
    return liquipedia_response["parse"]["text"]


@pytest.fixture(scope="session")
def league_matches_2026():
    """
    Every OpenDota league that played a match in 2026, with its full match list
    — 78 leagues and 29,000 matches, recorded 2026-08-10. This is the candidate
    pool the resolver ranks, and it is the whole pool rather than a shortlist so
    that the date window is shown doing the excluding.

    Match rows carry only the four fields the resolver reads — `leagueid`,
    `start_time`, `radiant_team_id`, `dire_team_id` — because the full rows are
    fourteen fields wide and none of the other ten resolve anything. `name` and
    `tier` per league come from `/leagues`.

    `known_team_ids` is the 43 teams in the dataset as it stood at 1,605
    matches: today's CSV less the Esports World Cup and 1win Essence II, which
    were backfilled afterwards. That is the state the resolution was designed
    against, and the only one in which those two events score below 100%.
    """
    return load_payload("league_matches_2026")


@pytest.fixture(scope="session")
def audit_2025():
    """
    The 2025 back catalogue as the coverage audit of 2026-08-12 saw it: the
    Tier 1 events from the dataset's first match to the end of that year, and
    the **full match list of every league that fell inside one of their
    windows** — the shortlist the audit's pro match walk produced, each league
    re-read in full, which is the pool the resolver actually ranked.

    A shortlist rather than the whole year's leagues, unlike
    `league_matches_2026`. So this does not show the date window doing the
    excluding; what it holds is the answer, league by league, on the half-season
    the 2026 fixture cannot speak for.

    That half-season is the one with the nested event in it. FISSURE PLAYGROUND
    2 ran entirely inside BLAST Slam IV's window, both are made of tracked
    teams, and the nested one played *more* matches — so BLAST Slam IV's window
    holds six candidates and is decided by window coverage alone.

    Three of the fifteen leagues are here to be *dropped*: the walk saw enough
    of the Ancients League, the European Pro League and the Snake Trophy to put
    each inside a window, and each spans months once re-read in full. They are
    what the confirmation pass is for, and the reason nothing may win on the
    walk's own summary.

    `known_team_ids` is the 50 teams in the dataset at 1,822 matches, which is
    the state the audit ran against. `ledger` is the verdict on each shortlisted
    league at that moment.
    """
    return load_payload("audit_2025")
