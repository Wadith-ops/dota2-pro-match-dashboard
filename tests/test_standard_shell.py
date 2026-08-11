"""
The two file shapes the pipeline writes, and the one-time backfill.

The shell is otherwise verified by running it — but these are not plumbing. An
append that turns out to rewrite brings back the 21 GB of disk writes the split
was for; a document write that is not atomic is how ten thousand hand-made
verdicts get lost to an interrupt; and the backfill is what stands between the
Standard store and a CSV rebuilt from one morning's matches.

Nothing here touches the network. Every path is pointed at pytest's `tmp_path`,
so the only filesystem this reaches is the one pytest made for it.
"""
import json

import pytest

import opendota_pipeline as pipeline
from core import extract_standard


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A Standard store of our own, so no test can read or write the real one."""
    path = tmp_path / "matches_standard.jsonl"
    monkeypatch.setattr(pipeline, "STANDARD_FILE", str(path))
    return path


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    """The 1 GB raw file, in miniature. Write to it with `legacy.write`."""
    path = tmp_path / "matches.json"
    monkeypatch.setattr(pipeline, "LEGACY_RAW_FILE", str(path))
    return path


@pytest.fixture
def raw_sink(tmp_path, monkeypatch):
    """The raw sink, and the seam that decides whether anything reaches it."""
    path = tmp_path / "matches_raw.jsonl"
    monkeypatch.setattr(pipeline, "RAW_FILE", str(path))
    return path


def match(match_id, **overrides):
    """A payload thin enough to read in a test and fat enough to be trimmed."""
    base = {
        "match_id"   : match_id,
        "duration"   : 2400,
        "start_time" : 1754000000,
        "objectives" : [{"type": "CHAT_MESSAGE_FIRSTBLOOD", "time": 90}],
        "league_name": "Slam IV",
        "players"    : [{"hero_id": 1, "kills": 5, "lane_pos": {"90": {"120": 3}}}],
        "chat"       : [{"key": "gg"}],
    }
    base.update(overrides)
    return base


def lines(path):
    return path.read_text(encoding="utf-8").splitlines()


class TestAppendingToTheStore:
    def test_a_record_per_match_is_written(self, store):
        pipeline.append_standard([match(1), match(2)])

        assert [json.loads(line)["match_id"] for line in lines(store)] == [1, 2]

    def test_what_is_written_is_the_extract_not_the_payload(self, store):
        pipeline.append_standard([match(1)])

        assert json.loads(lines(store)[0]) == extract_standard(match(1))
        assert "chat" not in json.loads(lines(store)[0])

    def test_a_second_append_adds_to_the_file_rather_than_replacing_it(self, store):
        # The whole point. The store this replaces was rewritten in full every
        # ten matches — 21 GB of writes to add 84 MB during one backfill.
        pipeline.append_standard([match(1)])
        pipeline.append_standard([match(2)])

        assert [json.loads(line)["match_id"] for line in lines(store)] == [1, 2]

    def test_appending_nothing_does_not_create_a_file(self, store):
        pipeline.append_standard([])

        assert not store.exists()

    def test_the_store_reads_back_as_what_was_written(self, store):
        pipeline.append_standard([match(1), match(2)])

        assert pipeline.read_standard_store() == [
            extract_standard(match(1)), extract_standard(match(2))
        ]

    def test_a_refetched_match_is_one_record_when_read(self, store):
        # Every suspect match is fetched again for five days, so this is the
        # ordinary case rather than an edge one.
        pipeline.append_standard([match(1, objectives=None)])
        pipeline.append_standard([match(1)])

        records = pipeline.read_standard_store()

        assert len(records) == 1
        assert records[0]["objectives"] is not None

    def test_a_store_that_is_not_there_reads_as_no_records(self, store):
        assert pipeline.read_standard_store() == []

    def test_a_damaged_line_is_reported_and_the_rest_still_read(
        self, store, capsys
    ):
        pipeline.append_standard([match(1)])
        pipeline.append_text(str(store), '{"match_id": 2, "objec\n')
        pipeline.append_standard([match(3)])

        records = pipeline.read_standard_store()

        assert [record["match_id"] for record in records] == [1, 3]
        assert "1 unreadable line" in capsys.readouterr().out


class TestTheRawSeam:
    def test_nothing_is_written_while_the_seam_is_shut(self, raw_sink, monkeypatch):
        monkeypatch.setattr(pipeline, "SAVE_RAW", False)

        pipeline.append_raw([match(1)])

        assert not raw_sink.exists()

    def test_the_untrimmed_payload_is_written_when_it_is_open(
        self, raw_sink, monkeypatch
    ):
        # A configuration change, not a rewrite: this is the acceptance
        # criterion that the pruning must not weld the sink shut.
        monkeypatch.setattr(pipeline, "SAVE_RAW", True)

        pipeline.append_raw([match(1)])

        assert json.loads(lines(raw_sink)[0]) == match(1)

    def test_the_sink_is_appended_to_like_the_store(self, raw_sink, monkeypatch):
        monkeypatch.setattr(pipeline, "SAVE_RAW", True)

        pipeline.append_raw([match(1)])
        pipeline.append_raw([match(2)])

        assert len(lines(raw_sink)) == 2


class TestSeedingFromTheLegacyStore:
    def write_legacy(self, path, matches):
        path.write_text(json.dumps(matches, indent=2), encoding="utf-8")

    def test_every_match_in_the_old_file_becomes_a_record(self, store, legacy):
        self.write_legacy(legacy, [match(1), match(2), match(3)])

        pipeline.backfill_standard_store()

        assert [record["match_id"] for record in pipeline.read_standard_store()] \
               == [1, 2, 3]

    def test_the_old_file_is_left_exactly_as_it_was(self, store, legacy):
        # Reconciling the store against the CSV and deleting the raw file is
        # `tier1-pipeline-automation/11`, and deliberately a human's decision.
        self.write_legacy(legacy, [match(1)])
        before = legacy.read_bytes()

        pipeline.backfill_standard_store()

        assert legacy.read_bytes() == before

    def test_a_run_with_a_store_already_there_does_nothing(self, store, legacy):
        # It runs at the top of every `main()`, so "does nothing the second
        # time" is what stops it appending the back catalogue twice a day.
        self.write_legacy(legacy, [match(1), match(2)])
        pipeline.backfill_standard_store()

        self.write_legacy(legacy, [match(3)])
        pipeline.backfill_standard_store()

        assert [record["match_id"] for record in pipeline.read_standard_store()] \
               == [1, 2]

    def test_a_machine_that_never_held_the_old_file_is_untouched(self, store, legacy):
        pipeline.backfill_standard_store()

        assert not store.exists()

    def test_a_backfill_larger_than_the_batch_writes_every_match(
        self, store, legacy, monkeypatch
    ):
        monkeypatch.setattr(pipeline, "STORE_BATCH", 3)
        self.write_legacy(legacy, [match(i) for i in range(10)])

        pipeline.backfill_standard_store()

        assert len(pipeline.read_standard_store()) == 10


class TestWritingDocuments:
    def test_the_contents_are_replaced(self, tmp_path):
        path = tmp_path / "ledger.json"
        path.write_text("old", encoding="utf-8")

        pipeline.write_text_atomic(str(path), "new")

        assert path.read_text(encoding="utf-8") == "new"

    def test_a_failed_write_leaves_the_previous_version_intact(
        self, tmp_path, monkeypatch
    ):
        # The window a plain `open(path, "w")` opens: it truncates before it
        # writes, so an interrupt anywhere in between leaves a file that parses
        # as nothing at all. Here the interrupt lands after the new contents are
        # written and before they are put in place, which is the whole of it.
        path = tmp_path / "ledger.json"
        path.write_text("old", encoding="utf-8")

        def interrupt(*args):
            raise KeyboardInterrupt("interrupted mid-write")

        monkeypatch.setattr(pipeline.os, "fsync", interrupt)

        with pytest.raises(KeyboardInterrupt):
            pipeline.write_text_atomic(str(path), "new")

        assert path.read_text(encoding="utf-8") == "old"

    def test_no_temporary_file_is_left_behind_by_a_successful_write(self, tmp_path):
        path = tmp_path / "ledger.json"

        pipeline.write_text_atomic(str(path), "new")

        assert [p.name for p in tmp_path.iterdir()] == ["ledger.json"]
