"""
What the main loop does when a call fails, and what it records having done.

Everything here is about *isolation*: one unreachable league must cost that
league's matches and nothing else, and one unfetchable match must cost that
match and nothing else. A run is fifteen leagues and a couple of thousand
matches, so anything that lets a single failure end the loop trades the whole
dataset for one bad response.

Nothing touches the network — `fetch_url` is replaced with a stub keyed by URL —
and every path points at pytest's `tmp_path`.
"""
import json
from datetime import datetime, timezone

import pytest

import core
import opendota_pipeline as pipeline

NOW = int(datetime.now(timezone.utc).timestamp())


def payload(match_id, **overrides):
    """A complete match: objectives recorded, a score, nothing suspect."""
    base = {
        "match_id"     : match_id,
        "duration"     : 900,
        "start_time"   : NOW,
        "radiant_score": 24,
        "dire_score"   : 18,
        "objectives"   : [{"type": "CHAT_MESSAGE_FIRSTBLOOD", "time": 90}],
        "players"      : [],
    }
    base.update(overrides)
    return base


@pytest.fixture
def opendota(monkeypatch):
    """
    Stands in for the API. Assign `opendota.leagues` as `{league_id: [match_id]}`
    and `opendota.matches` as `{match_id: payload}`; a None value in either is a
    call that failed after its retries, which is what `fetch_url` answers.
    """
    class Stub:
        def __init__(self):
            self.leagues = {}
            self.matches = {}

        def fetch(self, url, **kwargs):
            if "/leagues/" in url:
                league_id = int(url.split("/leagues/")[1].split("/")[0])
                match_ids = self.leagues.get(league_id)
                if match_ids is None:
                    return None
                return [{"match_id": match_id} for match_id in match_ids]

            return self.matches.get(int(url.rsplit("/", 1)[1]))

    stub = Stub()
    monkeypatch.setattr(pipeline, "fetch_url", stub.fetch)
    return stub


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """A store and two checkpoints of our own."""
    monkeypatch.setattr(pipeline, "SAVE_RAW", False)
    for name, attribute in (
        ("matches_standard.jsonl", "STANDARD_FILE"),
        ("fetched_matches.json", "MATCH_CHECKPOINT"),
        ("unparsed_matches.json", "UNPARSED_CHECKPOINT"),
    ):
        monkeypatch.setattr(pipeline, attribute, str(tmp_path / name))
    return tmp_path


def checkpointed(paths):
    return set(json.loads((paths / "fetched_matches.json").read_text()))


def by_league(records):
    return {record["league_id"]: record for record in records}


class TestNoSingleFailureEndsTheRun:
    def test_a_league_whose_match_list_fails_does_not_stop_the_next_league(
        self, opendota, paths
    ):
        opendota.leagues = {17419: None, 20009: [10]}
        opendota.matches = {10: payload(10)}

        records = pipeline.run_pipeline({17419: "Slam IV", 20009: "1win Essence II"})

        assert by_league(records)[20009]["complete"] == 1
        assert checkpointed(paths) == {10}

    def test_the_unreachable_league_is_recorded_as_failed_not_as_empty(
        self, opendota, paths
    ):
        # A league with no matches and a league that could not be reached are
        # different facts. Reporting both as zero is how a whole tournament goes
        # missing for two months without anything looking wrong.
        opendota.leagues = {17419: None, 20009: []}

        records = by_league(pipeline.run_pipeline(
            {17419: "Slam IV", 20009: "The International 2026"}
        ))

        assert records[17419]["list_failed"] is True
        assert records[20009]["list_failed"] is False
        assert records[20009]["available"] == 0

    def test_a_match_that_cannot_be_fetched_is_counted_and_the_rest_are_kept(
        self, opendota, paths
    ):
        opendota.leagues = {17419: [10, 11, 12]}
        opendota.matches = {10: payload(10), 11: None, 12: payload(12)}

        records = pipeline.run_pipeline({17419: "Slam IV"})

        assert by_league(records)[17419]["failed"] == 1
        assert by_league(records)[17419]["complete"] == 2

    def test_a_failed_match_stays_out_of_the_checkpoint_so_the_next_run_asks_again(
        self, opendota, paths
    ):
        # The same mechanism a held match uses: the absence of a checkpoint
        # entry *is* the retry. A failure that checkpointed the match would
        # lose it permanently to one bad response.
        opendota.leagues = {17419: [10, 11]}
        opendota.matches = {10: payload(10), 11: None}

        pipeline.run_pipeline({17419: "Slam IV"})

        assert checkpointed(paths) == {10}

    def test_a_league_that_failed_is_fetched_again_next_run(self, opendota, paths):
        opendota.leagues = {17419: None}
        pipeline.run_pipeline({17419: "Slam IV"})

        opendota.leagues = {17419: [10]}
        opendota.matches = {10: payload(10)}
        pipeline.run_pipeline({17419: "Slam IV"})

        assert checkpointed(paths) == {10}


class TestWhatTheRunRecords:
    def test_matches_already_fetched_are_counted_as_skipped_not_as_new(
        self, opendota, paths
    ):
        opendota.leagues = {17419: [10, 11]}
        opendota.matches = {10: payload(10), 11: payload(11)}
        pipeline.run_pipeline({17419: "Slam IV"})

        records = by_league(pipeline.run_pipeline({17419: "Slam IV"}))

        assert records[17419]["available"] == 2
        assert records[17419]["skipped"] == 2
        assert records[17419]["complete"] == 0

    def test_a_held_match_is_recorded_as_held(self, opendota, paths):
        # Suspect and young enough that OpenDota may still parse the replay.
        opendota.leagues = {17419: [10]}
        opendota.matches = {10: payload(10, objectives=[])}

        records = by_league(pipeline.run_pipeline({17419: "Slam IV"}))

        assert records[17419]["held"] == 1
        assert checkpointed(paths) == set()

    def test_the_report_names_every_league_and_its_counts(
        self, opendota, paths, capsys
    ):
        opendota.leagues = {17419: [10], 20009: None}
        opendota.matches = {10: payload(10)}

        pipeline.run_pipeline({17419: "Slam IV", 20009: "1win Essence II"})

        printed = capsys.readouterr().out
        assert "Slam IV" in printed
        assert "1win Essence II" in printed
        assert "match list fetch failed" in printed

    def test_a_match_id_listed_twice_is_skipped_the_second_time_not_failed(
        self, opendota, paths
    ):
        # `get_match_detail` answers None both for a match it skipped and for
        # one it could not fetch. Reading the count off that value would report
        # a duplicate id as a broken fetch, in the very line a run is judged by.
        opendota.leagues = {17419: [10, 10, 11]}
        opendota.matches = {10: payload(10), 11: payload(11)}

        records = by_league(pipeline.run_pipeline({17419: "Slam IV"}))

        assert records[17419]["failed"] == 0
        assert records[17419]["complete"] == 2

    def test_a_run_with_no_active_leagues_still_summarises_to_zero(self, paths):
        # `main` prints the summary from whatever this returns, so returning
        # nothing at all would make an idle run indistinguishable from a crash.
        records = pipeline.run_pipeline({})

        assert records == []
        assert core.summarise_run(records)["fetched"] == 0

    def test_the_summary_names_the_league_that_could_not_be_reached(
        self, opendota, paths
    ):
        # The table says which league too, but the table is on stdout and this
        # line is what reaches the log. "One league list failed" in a log
        # nobody was watching is a question, not an answer.
        opendota.leagues = {19719: None}

        records = pipeline.run_pipeline({19719: "The International 2026"})

        assert "The International 2026" in core.format_run_summary(
            core.summarise_run(records)
        )


class TestARunSaysWhetherItFinished:
    """
    The summary line is printed after everything that could fail, so its
    presence in the output means the run reached the end of `main()` — which is
    the whole of ADR-0011's contract with the scheduled job, and is a property
    of *where* the line is printed rather than of what it says.
    """

    @pytest.fixture
    def stubbed_main(self, monkeypatch, tmp_path):
        """Every step of `main()` replaced. No network, no files, no CSV."""
        # Including the resolver's daily marker. `main()` writes it, and a run
        # writing into the working copy's own `checkpoints/` would switch the
        # resolver off for the rest of the day on the machine running the tests.
        monkeypatch.setattr(pipeline, "RESOLUTION_CHECKPOINT",
                            str(tmp_path / "last_resolution.json"))
        monkeypatch.setattr(pipeline, "ensure_directories", lambda: None)
        monkeypatch.setattr(pipeline, "backfill_standard_store", lambda: None)
        monkeypatch.setattr(pipeline, "get_patch_map", lambda: {})
        monkeypatch.setattr(pipeline, "get_patch_releases", lambda: [])
        monkeypatch.setattr(pipeline, "fetch_all_leagues", lambda: [])
        monkeypatch.setattr(pipeline, "load_ledger", lambda leagues: {"leagues": []})
        monkeypatch.setattr(pipeline, "run_pipeline", lambda leagues: [])
        monkeypatch.setattr(pipeline, "build_dataframe",
                            lambda patch_map, releases: (None, []))
        monkeypatch.setattr(
            pipeline, "resolve_tier1_leagues",
            lambda *args: print("resolving Tier 1 events..."),
        )
        return monkeypatch

    def test_the_summary_is_the_last_thing_a_finished_run_prints(
        self, stubbed_main, capsys
    ):
        pipeline.main()

        printed = capsys.readouterr().out
        assert core.read_run_summary(printed)
        assert printed.strip().splitlines()[-1].startswith(core.RUN_SUMMARY_PREFIX)

    def test_a_run_that_falls_over_after_fetching_prints_no_summary(
        self, stubbed_main, capsys
    ):
        # Which is what makes the line's *absence* meaningful. The scheduled
        # job logs that absence rather than a summary that would claim the run
        # had finished.
        def explode(*args):
            raise RuntimeError("the resolver fell over")

        stubbed_main.setattr(pipeline, "resolve_tier1_leagues", explode)

        with pytest.raises(RuntimeError):
            pipeline.main()

        assert core.read_run_summary(capsys.readouterr().out) is None
