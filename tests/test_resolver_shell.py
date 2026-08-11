"""
The resolver's shell: the pro match walk, and when the resolution file is
rewritten.

Most of the pipeline shell is verified by running it. These two are not
plumbing — each is a decision with a cost attached. A walk that stops too early
misses a tournament, and a resolution file rewritten every run puts a commit in
front of Wade every day saying nothing.

What a rate limit does to the walk is `fetch_url`'s business, and is tested with
the rest of the retry policy in `test_fetch_retry.py`. How far back the walk
goes is decided by `core.resolution_walk_start`, which is arithmetic over plain
data and is tested in `test_resolver.py` with the rest of the core.

Nothing here touches the network. `fetch_url` is replaced with a stub and the
resolution path points at pytest's `tmp_path`.
"""
import json
from datetime import datetime, timezone

import pytest

import opendota_pipeline as pipeline


def pro_match(match_id, start_time, league_id=19785):
    """One row of `/proMatches`, trimmed to what the walk reads."""
    return {"match_id": match_id, "start_time": start_time, "leagueid": league_id,
            "radiant_team_id": 15, "dire_team_id": 39}


def at(day):
    """Midday UTC on an ISO date, as a unix timestamp."""
    return int(datetime.fromisoformat(f"{day}T12:00:00+00:00").timestamp())


@pytest.fixture
def opendota(monkeypatch):
    """
    The pages `fetch_pro_matches` sees, newest first. Assign `opendota.pages` to
    change them; a None in the list is a failed request. Every URL asked for is
    recorded on `opendota.urls`.
    """
    class Stub:
        # Set on the instance, not the class. A list held as a class attribute
        # is one list for the whole session, and `urls.append` would then carry
        # the previous test's calls into this one's assertions.
        def __init__(self):
            self.pages = []
            self.urls  = []

        def fetch(self, url):
            self.urls.append(url)
            return self.pages.pop(0) if self.pages else []

    stub = Stub()
    monkeypatch.setattr(pipeline, "fetch_url", stub.fetch)
    return stub


class TestWalkingBackThroughProMatches:
    def test_it_stops_at_the_first_page_reaching_past_the_cutoff(self, opendota):
        opendota.pages = [
            [pro_match(300, at("2026-08-09"))],
            [pro_match(200, at("2026-08-02"))],
            [pro_match(100, at("2026-07-20"))],
        ]

        matches = pipeline.fetch_pro_matches(at("2026-08-01"))

        # Three pages, not two: the walk has to read a page that goes past the
        # cutoff to know it has covered everything down to it. That page is kept
        # whole rather than trimmed — it is already paid for.
        assert [m["match_id"] for m in matches] == [300, 200, 100]
        assert len(opendota.pages) == 0

    def test_each_page_asks_for_matches_older_than_the_last(self, opendota):
        opendota.pages = [
            [pro_match(300, at("2026-08-09")), pro_match(280, at("2026-08-08"))],
            [pro_match(200, at("2026-07-01"))],
        ]

        pipeline.fetch_pro_matches(at("2026-06-01"))

        assert opendota.urls[0].endswith("/proMatches")
        assert opendota.urls[1].endswith("less_than_match_id=280")

    def test_an_empty_page_is_the_end_of_the_list_not_a_failure(self, opendota):
        # There is always a last page, and OpenDota answers it with []. Reading
        # that as a failure would report the walk as broken every time it
        # reached the beginning of recorded history.
        opendota.pages = [[pro_match(300, at("2026-08-09"))], []]

        assert len(pipeline.fetch_pro_matches(at("2020-01-01"))) == 1

    def test_a_failed_page_ends_the_walk_with_what_it_already_has(self, opendota):
        # A short walk costs a missed candidate, never a wrong one: every
        # shortlisted league is re-read in full before it can win a window.
        opendota.pages = [[pro_match(300, at("2026-08-09"))], None]

        assert len(pipeline.fetch_pro_matches(at("2020-01-01"))) == 1

    def test_the_page_cap_stops_a_walk_that_would_never_reach_its_cutoff(
        self, opendota, monkeypatch
    ):
        monkeypatch.setattr(pipeline, "PRO_MATCHES_MAX_PAGES", 3)
        opendota.pages = [[pro_match(300 - n, at("2026-08-09"))] for n in range(10)]

        assert len(pipeline.fetch_pro_matches(at("2020-01-01"))) == 3


class TestWritingTheResolution:
    RECORDS = [{"event": "The International 2026", "start_date": "2026-08-13",
                "end_date": "2026-08-23", "league_id": None, "league_name": None,
                "verdict": None, "ambiguous": False, "candidates": []}]

    @pytest.fixture
    def resolution_file(self, tmp_path, monkeypatch):
        path = tmp_path / "tier1_resolution.json"
        monkeypatch.setattr(pipeline, "RESOLUTION_FILE", str(path))
        return path

    def test_a_first_resolution_is_written(self, resolution_file):
        pipeline.write_resolution(
            self.RECORDS, datetime(2026, 8, 11, tzinfo=timezone.utc), None
        )

        stored = json.loads(resolution_file.read_text(encoding="utf-8"))
        assert stored["events"] == self.RECORDS
        assert stored["gap_count"] == 1

    def test_an_unchanged_answer_leaves_the_file_alone(self, resolution_file):
        # It is committed and pushed. A run that re-derived the same mapping
        # would otherwise commit a new timestamp every morning and nothing else.
        written = pipeline.write_resolution(
            self.RECORDS, datetime(2026, 8, 11, tzinfo=timezone.utc), None
        )
        before = resolution_file.read_text(encoding="utf-8")

        pipeline.write_resolution(
            self.RECORDS, datetime(2026, 8, 12, tzinfo=timezone.utc), written
        )

        assert resolution_file.read_text(encoding="utf-8") == before

    def test_a_changed_answer_is_written_with_the_new_clock(self, resolution_file):
        written = pipeline.write_resolution(
            self.RECORDS, datetime(2026, 8, 11, tzinfo=timezone.utc), None
        )
        resolved = [dict(self.RECORDS[0], league_id=19719, verdict="active")]

        pipeline.write_resolution(
            resolved, datetime(2026, 8, 14, tzinfo=timezone.utc), written
        )

        stored = json.loads(resolution_file.read_text(encoding="utf-8"))
        assert stored["events"][0]["league_id"] == 19719
        assert stored["generated_at"].startswith("2026-08-14")

    def test_an_unreadable_file_reads_as_nothing_and_is_replaced(
        self, resolution_file
    ):
        # Nothing in it is a decision — it is derived from the ledger and the
        # calendar on every run. Unlike the ledger, there is nothing to lose.
        resolution_file.write_text("not json at all", encoding="utf-8")

        assert pipeline.read_resolution() is None

        pipeline.write_resolution(
            self.RECORDS, datetime(2026, 8, 11, tzinfo=timezone.utc),
            pipeline.read_resolution(),
        )

        assert json.loads(resolution_file.read_text(encoding="utf-8"))["events"]


class TestTheWholeStepRunsEndToEnd:
    """
    One smoke test over `resolve_tier1_leagues`, the function where the calendar,
    the walk, the confirmation pass, the ledger and the report meet.

    It earns its place: every piece below it is tested, and the orchestrator
    still shipped a `NameError` on a live run — a function moved into the core
    and one call site left behind. Nothing that asserted on returned data could
    have caught it, because the fault was that no data was returned at all.
    """

    EVENT = {"name": "1win Essence II", "start_date": "2026-07-30",
             "end_date": "2026-08-05"}
    LEDGER = {"leagues": [{"league_id": 20009, "name": "1win Essence II",
                           "tier1_event": None, "verdict": "pending"}]}
    API_LEAGUES = [{"leagueid": 20009, "name": "1win Essence II",
                    "tier": "professional"}]

    @pytest.fixture
    def wired(self, tmp_path, monkeypatch):
        """Every collaborator replaced: no network, no real files."""
        monkeypatch.setattr(pipeline, "LEDGER_FILE", str(tmp_path / "leagues.json"))
        monkeypatch.setattr(
            pipeline, "RESOLUTION_FILE", str(tmp_path / "tier1_resolution.json")
        )
        monkeypatch.setattr(
            pipeline.liquipedia, "get_tier1_events",
            lambda: {"events": [self.EVENT], "source": "cache"},
        )

        matches = [pro_match(8931183918 + n, at("2026-08-0" + str(n % 5 + 1)),
                             league_id=20009)
                   for n in range(4)]

        def fetch(url):
            if "proMatches" in url:
                return matches if "less_than" not in url else []
            return matches

        monkeypatch.setattr(pipeline, "fetch_url", fetch)
        return tmp_path

    def test_it_maps_the_league_and_writes_both_records(self, wired, capsys):
        pipeline.resolve_tier1_leagues(
            self.LEDGER, self.API_LEAGUES, {15, 39},
            datetime(2026, 8, 11, tzinfo=timezone.utc),
        )

        ledger = json.loads((wired / "leagues.json").read_text(encoding="utf-8"))
        assert ledger["leagues"][0]["tier1_event"] == "1win Essence II"

        report = json.loads(
            (wired / "tier1_resolution.json").read_text(encoding="utf-8")
        )
        assert report["events"][0]["league_id"] == 20009
        assert report["gap_count"] == 0

    def test_a_league_nobody_has_judged_is_shouted_about(self, wired, capsys):
        # The whole feature in one line: a Tier 1 event whose matches are not
        # being fetched, found by the pipeline rather than by a friend asking
        # why a team is missing.
        pipeline.resolve_tier1_leagues(
            self.LEDGER, self.API_LEAGUES, {15, 39},
            datetime(2026, 8, 11, tzinfo=timezone.utc),
        )

        assert "ATTENTION" in capsys.readouterr().out

    def test_nothing_is_fetched_when_every_event_is_already_mapped(
        self, wired, monkeypatch
    ):
        monkeypatch.setattr(pipeline, "fetch_url", lambda url: pytest.fail(
            "the resolver walked for events that are already mapped"
        ))
        mapped = {"leagues": [dict(self.LEDGER["leagues"][0],
                                   tier1_event="1win Essence II")]}

        pipeline.resolve_tier1_leagues(
            mapped, self.API_LEAGUES, {15, 39},
            datetime(2026, 8, 11, tzinfo=timezone.utc),
        )

    def test_a_failed_league_list_skips_resolution_rather_than_guessing(
        self, wired, monkeypatch
    ):
        monkeypatch.setattr(pipeline, "fetch_url", lambda url: pytest.fail(
            "the resolver walked without the tiers it needs to rank"
        ))

        pipeline.resolve_tier1_leagues(
            self.LEDGER, None, {15, 39},
            datetime(2026, 8, 11, tzinfo=timezone.utc),
        )

        report = json.loads(
            (wired / "tier1_resolution.json").read_text(encoding="utf-8")
        )
        assert report["events"][0]["league_id"] is None


class TestTheLeagueListIsFetchedOnce:
    """
    `main` fetches `/leagues` once and hands the same response to the ledger and
    to the resolver. Ten thousand leagues is a megabyte; fetching it twice to
    give two callers the same list is a call spent on nothing.
    """

    def test_a_supplied_league_list_is_not_fetched_again(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pipeline, "LEDGER_FILE", str(tmp_path / "leagues.json"))
        monkeypatch.setattr(pipeline, "fetch_all_leagues", lambda: pytest.fail(
            "load_ledger refetched a league list it was handed"
        ))

        pipeline.load_ledger([{"leagueid": 17419, "name": "SLAM IV",
                               "tier": "professional"}])

        assert (tmp_path / "leagues.json").exists()

    def test_a_supplied_none_means_the_fetch_failed_not_that_none_was_supplied(
        self, tmp_path, monkeypatch
    ):
        # The two must not collapse. `None` here is a failed fetch, so there is
        # no discovery this run — and nothing is seeded over an existing ledger.
        ledger_file = tmp_path / "leagues.json"
        ledger_file.write_text(
            json.dumps({"leagues": [{"league_id": 17419, "name": "Slam IV",
                                     "tier1_event": None, "verdict": "active"}]}),
            encoding="utf-8",
        )
        monkeypatch.setattr(pipeline, "LEDGER_FILE", str(ledger_file))
        monkeypatch.setattr(pipeline, "fetch_all_leagues", lambda: pytest.fail(
            "load_ledger refetched after being told the fetch failed"
        ))
        before = ledger_file.read_text(encoding="utf-8")

        pipeline.load_ledger(None)

        assert ledger_file.read_text(encoding="utf-8") == before
