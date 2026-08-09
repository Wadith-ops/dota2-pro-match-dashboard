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
def patch_map():
    """The subset of the OpenDota patch constants the fixtures need."""
    return {58: "7.39", 59: "7.40", 60: "7.41"}


@pytest.fixture
def liquipedia_response():
    """
    The full MediaWiki `action=parse` envelope for Tier 1 Tournaments, recorded
    2026-08-09. Kept whole rather than trimmed to the table, so the client's
    unwrapping of `parse.text` is exercised by the same fixture as the parser.
    """
    return load_payload("liquipedia_tier1")


@pytest.fixture
def liquipedia_tier1_html(liquipedia_response):
    """Just the rendered HTML, which is what the pure parser takes."""
    return liquipedia_response["parse"]["text"]
