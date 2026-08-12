"""
The shell around hotfix resolution: fetching Valve's patch list, and rebuilding
the CSV without blanking a column when that fetch fails.

The rule these hold is that **a patch list that cannot be fetched never erases a
hotfix already recorded**. The CSV is rebuilt whole every run, which is what
makes the Standard store the single source of truth and also what would let one
unreachable host lose a whole column. `read_hotfix_column` is the guard.

Nothing here touches the network: `fetch_url` is replaced, and every file is
under `tmp_path`.
"""
import pandas as pd
import pytest

import core
import opendota_pipeline as pipeline


class TestFetchingValvesPatchList:
    def test_a_successful_fetch_becomes_an_ascending_release_table(
        self, valve_patch_list, monkeypatch
    ):
        monkeypatch.setattr(pipeline, "fetch_url",
                            lambda url, **kwargs: valve_patch_list)

        releases = pipeline.get_patch_releases()

        assert len(releases) == 117
        assert releases == sorted(releases)

    def test_it_goes_to_valve_rather_than_to_opendota(
        self, valve_patch_list, monkeypatch
    ):
        # The only non-OpenDota endpoint the pipeline reaches, and it still goes
        # through `fetch_url` — which is where the timeout and the retry
        # schedule live.
        asked = []

        def fetch(url, **kwargs):
            asked.append(url)
            return valve_patch_list

        monkeypatch.setattr(pipeline, "fetch_url", fetch)
        pipeline.get_patch_releases()

        assert asked == [pipeline.PATCH_LIST_URL]
        assert "dota2.com" in pipeline.PATCH_LIST_URL

    def test_a_failed_fetch_resolves_nothing_rather_than_raising(self, monkeypatch):
        monkeypatch.setattr(pipeline, "fetch_url", lambda url, **kwargs: None)

        assert pipeline.get_patch_releases() == []

    def test_an_unsuccessful_payload_is_a_failure_whatever_the_status_was(
        self, monkeypatch
    ):
        # The datafeed reports success in a field, so a 200 is not enough — the
        # same trap the MediaWiki endpoint sets.
        monkeypatch.setattr(pipeline, "fetch_url",
                            lambda url, **kwargs: {"success": False, "patches": []})

        assert pipeline.get_patch_releases() == []

    def test_a_list_rather_than_the_envelope_is_a_failure(self, monkeypatch):
        monkeypatch.setattr(pipeline, "fetch_url",
                            lambda url, **kwargs: [{"patch_name": "7.40"}])

        assert pipeline.get_patch_releases() == []

    def test_an_empty_patch_list_is_said_out_loud(self, monkeypatch, capsys):
        # There is no such thing as a Dota patch list with nothing in it — the
        # rule the patch constants and the league list already follow.
        monkeypatch.setattr(pipeline, "fetch_url",
                            lambda url, **kwargs: {"success": True, "patches": []})

        assert pipeline.get_patch_releases() == []
        assert "no usable releases" in capsys.readouterr().out


@pytest.fixture
def csv_file(tmp_path, monkeypatch):
    """The CSV the pipeline reads its own hotfix column back out of."""
    path = tmp_path / "matches_flat.csv"
    monkeypatch.setattr(pipeline, "CSV_FILE", str(path))
    return path


def write_csv(path, rows):
    pd.DataFrame(rows).to_csv(path, index=False)


class TestReadingTheHotfixColumnBack:
    def test_it_reads_what_the_last_run_recorded(self, csv_file):
        write_csv(csv_file, [{"match_id": 1, "patch_hotfix": "7.40c"},
                             {"match_id": 2, "patch_hotfix": "7.41"}])

        assert pipeline.read_hotfix_column() == {1: "7.40c", 2: "7.41"}

    def test_an_unlettered_name_comes_back_a_string_not_a_float(self, csv_file):
        # "7.40" re-infers as 7.4 without `CSV_DTYPES`, and 7.4 carried forward
        # would put a float in a column of names.
        write_csv(csv_file, [{"match_id": 1, "patch_hotfix": "7.40"}])

        assert pipeline.read_hotfix_column() == {1: "7.40"}

    def test_a_blank_cell_is_not_carried_forward_as_a_value(self, csv_file):
        # Blank reads back as NaN, and NaN is not a hotfix.
        write_csv(csv_file, [{"match_id": 1, "patch_hotfix": None},
                             {"match_id": 2, "patch_hotfix": "7.41"}])

        assert pipeline.read_hotfix_column() == {2: "7.41"}

    def test_no_csv_yet_is_nothing_to_carry_forward(self, csv_file):
        assert pipeline.read_hotfix_column() == {}

    def test_a_csv_without_the_column_is_nothing_to_carry_forward(self, csv_file):
        # Every CSV written before this issue. The first run after it resolves
        # the whole column from scratch.
        write_csv(csv_file, [{"match_id": 1, "patch_label": "7.40"}])

        assert pipeline.read_hotfix_column() == {}

    def test_a_ragged_line_costs_nothing_because_only_two_columns_are_read(
        self, csv_file
    ):
        # Recorded rather than guarded against: asking for two columns by name
        # is what makes a stray field on one line somebody else's problem.
        csv_file.write_text("match_id,patch_hotfix\n1,7.40c\n2,7.41,extra\n",
                            encoding="utf-8")

        assert pipeline.read_hotfix_column() == {1: "7.40c", 2: "7.41"}

    def test_an_unreadable_file_is_reported_and_survived(self, csv_file, capsys):
        # Nothing here may stop a run: the column this protects is a nicety
        # beside the dataset it is a column of.
        csv_file.write_bytes(b"\xff\xfe\x00 not a csv at all")

        assert pipeline.read_hotfix_column() == {}
        assert "nothing to carry forward" in capsys.readouterr().out


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A Standard store holding one match, played well inside 7.40c."""
    path = tmp_path / "matches_standard.jsonl"
    monkeypatch.setattr(pipeline, "STANDARD_FILE", str(path))
    monkeypatch.setattr(pipeline, "META_FILE", str(tmp_path / "meta.json"))
    monkeypatch.setattr(
        pipeline, "read_standard_store",
        lambda: [{"match_id": 1, "patch": 59, "start_time": 1770000000,
                  "objectives": [{"type": "CHAT_MESSAGE_FIRSTBLOOD", "time": 90}],
                  "radiant_score": 20, "dire_score": 15}],
    )
    return path


class TestRebuildingTheCsvNeverBlanksTheColumn:
    def test_a_run_with_a_release_table_writes_the_hotfix(
        self, store, csv_file, patch_map, patch_release_table
    ):
        pipeline.build_dataframe(patch_map, patch_release_table)

        written = pd.read_csv(csv_file, dtype=core.CSV_DTYPES)
        assert written["patch_hotfix"].tolist() == ["7.40c"]

    def test_a_run_that_could_not_fetch_the_list_keeps_what_was_there(
        self, store, csv_file, patch_map, capsys
    ):
        # The acceptance criterion in one test: an unreachable host costs the
        # newest matches their hotfix, never every match its hotfix.
        write_csv(csv_file, [{"match_id": 1, "patch_hotfix": "7.40c"}])

        pipeline.build_dataframe(patch_map, [])

        written = pd.read_csv(csv_file, dtype=core.CSV_DTYPES)
        assert written["patch_hotfix"].tolist() == ["7.40c"]
        assert "1 hotfix label(s) kept from the previous CSV" in capsys.readouterr().out

    def test_a_run_with_no_release_table_and_no_history_leaves_it_blank(
        self, store, csv_file, patch_map
    ):
        # Blank rather than wrong. The next successful run fills it.
        pipeline.build_dataframe(patch_map, [])

        written = pd.read_csv(csv_file, dtype=core.CSV_DTYPES)
        assert written["patch_hotfix"].isna().all()

    def test_the_release_table_is_optional(self, store, csv_file, patch_map):
        # `audit_coverage` builds rows for their team ids and has no patch list.
        pipeline.build_dataframe(patch_map)

        assert pd.read_csv(csv_file, dtype=core.CSV_DTYPES)["patch_hotfix"].isna().all()
