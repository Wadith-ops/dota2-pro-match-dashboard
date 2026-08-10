"""
Loading the ledger: the one piece of the pipeline shell that is tested.

The shell is otherwise verified by running it, not by unit tests — but
`load_ledger` is not plumbing. It decides whether to seed a ledger, whether to
write one, and whether to fetch anything at all, and issue 06 asks for ledger
loading to be covered. The failure it guards against is the expensive kind: a
ledger written over is ten thousand verdicts gone, and a ledger seeded from an
empty response is a pipeline that covers nothing.

Nothing here touches the network. `fetch_all_leagues` is replaced with a stub
and `LEDGER_FILE` is pointed at pytest's `tmp_path`, so the only filesystem
this reaches is the one pytest made for it.
"""
import json

import pytest

import opendota_pipeline as pipeline
from core import LEDGER_ACTIVE, LEDGER_PENDING, LEDGER_REJECTED, active_leagues

API_LEAGUES = [
    {"leagueid": 17419, "name": "SLAM IV", "tier": "professional"},
    {"leagueid": 19364, "name": "WAIFUS CUP SEASON 4", "tier": "excluded"},
]


@pytest.fixture
def ledger_file(tmp_path, monkeypatch):
    """A ledger path of our own, so no test can read or write the real one."""
    path = tmp_path / "leagues.json"
    monkeypatch.setattr(pipeline, "LEDGER_FILE", str(path))
    return path


@pytest.fixture
def opendota(monkeypatch):
    """
    The league list `load_ledger` sees. Assign `opendota.leagues` to change the
    response; assign None to make the fetch fail.
    """
    class Stub:
        leagues = list(API_LEAGUES)

    stub = Stub()
    monkeypatch.setattr(pipeline, "fetch_all_leagues", lambda: stub.leagues)
    return stub


def write(path, ledger):
    path.write_text(json.dumps(ledger), encoding="utf-8")


def stored(path):
    return json.loads(path.read_text(encoding="utf-8"))


class TestSeedingWhenThereIsNoLedger:
    def test_a_ledger_is_written_with_every_league_rejected(self, ledger_file, opendota):
        ledger = pipeline.load_ledger()

        assert [e["verdict"] for e in stored(ledger_file)["leagues"]] == [
            LEDGER_REJECTED, LEDGER_REJECTED
        ]
        assert ledger == stored(ledger_file)

    def test_nothing_is_fetched_on_the_run_that_seeds(self, ledger_file, opendota):
        # Which leagues to cover is a judgement. A pipeline that guessed would
        # be the hardcoded dict again, and one that guessed *wrong* would spend
        # an afternoon of API calls on 10,000 leagues nobody asked for.
        assert active_leagues(pipeline.load_ledger()) == {}

    def test_an_empty_league_list_seeds_nothing_and_writes_nothing(
        self, ledger_file, opendota
    ):
        # The opposite of the rule the match loop follows. An empty *match*
        # list is a real answer from a league that has not started; an empty
        # *league* list is not, because OpenDota has ten thousand. Seeding on
        # it writes a ledger holding nothing, and the next run then greets
        # every real league as newly discovered.
        opendota.leagues = []

        assert active_leagues(pipeline.load_ledger()) == {}
        assert not ledger_file.exists()

    def test_a_failed_league_fetch_seeds_nothing_and_writes_nothing(
        self, ledger_file, opendota
    ):
        opendota.leagues = None

        assert active_leagues(pipeline.load_ledger()) == {}
        assert not ledger_file.exists()


class TestLoadingAnExistingLedger:
    def test_the_active_leagues_are_the_ones_fetched(self, ledger_file, opendota):
        write(ledger_file, {"leagues": [
            {"league_id": 17419, "name": "Slam IV", "verdict": LEDGER_ACTIVE},
            {"league_id": 19364, "name": "WAIFUS CUP SEASON 4",
             "verdict": LEDGER_REJECTED},
        ]})

        assert active_leagues(pipeline.load_ledger()) == {17419: "Slam IV"}

    def test_an_unchanged_ledger_is_not_rewritten(self, ledger_file, opendota):
        # It is a 10,000-line file and this runs daily. Rewriting it on every
        # run would put a commit in front of Wade every morning saying nothing.
        write(ledger_file, {"leagues": [
            {"league_id": 17419, "name": "Slam IV", "tier1_event": None,
             "verdict": LEDGER_ACTIVE},
            {"league_id": 19364, "name": "WAIFUS CUP SEASON 4",
             "tier1_event": None, "verdict": LEDGER_REJECTED},
        ]})
        before = ledger_file.read_text(encoding="utf-8")

        pipeline.load_ledger()

        assert ledger_file.read_text(encoding="utf-8") == before

    def test_a_curated_name_survives_a_rename_upstream(self, ledger_file, opendota):
        # OpenDota calls 17419 "SLAM IV". The dataset has called it "Slam IV"
        # for 96 matches, and `league_name` is what the dashboard groups on.
        write(ledger_file, {"leagues": [
            {"league_id": 17419, "name": "Slam IV", "verdict": LEDGER_ACTIVE},
        ]})

        assert active_leagues(pipeline.load_ledger()) == {17419: "Slam IV"}


class TestDiscovery:
    def test_a_new_league_is_recorded_as_pending(self, ledger_file, opendota):
        write(ledger_file, {"leagues": [
            {"league_id": 17419, "name": "Slam IV", "verdict": LEDGER_ACTIVE},
        ]})
        opendota.leagues = API_LEAGUES + [{"leagueid": 20100, "name": "New Cup"}]

        pipeline.load_ledger()

        by_id = {e["league_id"]: e["verdict"] for e in stored(ledger_file)["leagues"]}
        assert by_id == {17419: LEDGER_ACTIVE, 19364: LEDGER_PENDING,
                         20100: LEDGER_PENDING}

    def test_a_failed_league_fetch_leaves_the_ledger_alone(self, ledger_file, opendota):
        # Discovery going quiet must never stop match fetching, and must never
        # be mistaken for OpenDota having dropped every league it holds.
        write(ledger_file, {"leagues": [
            {"league_id": 17419, "name": "Slam IV", "verdict": LEDGER_ACTIVE},
        ]})
        before = ledger_file.read_text(encoding="utf-8")
        opendota.leagues = None

        assert active_leagues(pipeline.load_ledger()) == {17419: "Slam IV"}
        assert ledger_file.read_text(encoding="utf-8") == before


class TestTheLeagueListFetch:
    """
    `fetch_all_leagues` guards the shape of what came back, because everything
    downstream of it assumes a list of dicts.
    """

    def test_a_list_is_passed_through(self, monkeypatch):
        monkeypatch.setattr(pipeline, "fetch_url", lambda url: API_LEAGUES)

        assert pipeline.fetch_all_leagues() == API_LEAGUES

    def test_a_failed_fetch_stays_a_failed_fetch(self, monkeypatch):
        monkeypatch.setattr(pipeline, "fetch_url", lambda url: None)

        assert pipeline.fetch_all_leagues() is None

    def test_an_error_object_counts_as_a_failure(self, monkeypatch):
        # OpenDota answers 200 with an error object on a bad day. Handed on to
        # the ledger, a dict iterates as its own keys — an AttributeError
        # halfway through a run rather than a fetch that said it had failed.
        monkeypatch.setattr(pipeline, "fetch_url", lambda url: {"error": "rate limit"})

        assert pipeline.fetch_all_leagues() is None


class TestARuinedLedgerIsNeverWrittenOver:
    """
    The expensive failure. Every verdict in the file survives a syntax error
    and none of it survives being written over, so a ledger that will not parse
    stops the run rather than being replaced by a freshly discovered one.
    """

    def test_a_truncated_file_is_left_exactly_as_it_is(self, ledger_file, opendota):
        ledger_file.write_text('{"leagues": [{"league_id": 17419, "nam',
                               encoding="utf-8")
        before = ledger_file.read_text(encoding="utf-8")

        assert active_leagues(pipeline.load_ledger()) == {}
        assert ledger_file.read_text(encoding="utf-8") == before

    def test_valid_json_of_the_wrong_shape_is_left_alone_too(
        self, ledger_file, opendota
    ):
        # `{"leagues": {}}` parses, and reads as no leagues. Accepting it would
        # let discovery append all 10,050 as pending over a file whose verdicts
        # are one keystroke from being recovered.
        ledger_file.write_text('{"leagues": {}}', encoding="utf-8")

        assert active_leagues(pipeline.load_ledger()) == {}
        assert ledger_file.read_text(encoding="utf-8") == '{"leagues": {}}'

    def test_nothing_is_fetched_from_a_ruined_ledger(self, ledger_file, opendota):
        ledger_file.write_text("not json at all", encoding="utf-8")

        assert active_leagues(pipeline.load_ledger()) == {}
