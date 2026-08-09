"""
The contract between the CSV the pipeline writes and the dashboard that reads it.

Two things have to hold across that boundary: `patch_label` comes back a string
rather than a re-inferred float, and patch labels order by release even when a
name is not a plain number. Both used to be the dashboard's problem, patched up
with `f"{x:.2f}"` on every read; they are the pipeline's problem now.

The round trip goes through an in-memory buffer, so this file touches the
filesystem no more than the rest of the suite does.
"""
import io

import pandas as pd

from core import CSV_DTYPES, build_rows, patch_sort_key


def round_trip(rows):
    """Writes rows to CSV and reads them back the way the dashboard does."""
    buffer = io.StringIO()
    pd.DataFrame(rows).to_csv(buffer, index=False)
    buffer.seek(0)
    return pd.read_csv(buffer, dtype=CSV_DTYPES)


class TestCsvRoundTrip:
    def test_patch_label_survives_as_a_string(self, patch_map):
        rows = build_rows([{"match_id": 1, "patch": 59}], patch_map)

        df = round_trip(rows)

        assert df["patch_label"].tolist() == ["7.40"]

    def test_patch_label_would_be_re_inferred_as_a_float_without_the_dtype(
        self, patch_map
    ):
        # Why CSV_DTYPES exists. Read without it, "7.40" comes back as 7.4 and
        # the trailing zero is gone — the defect this issue fixes at source.
        rows = build_rows([{"match_id": 1, "patch": 59}], patch_map)
        buffer = io.StringIO()
        pd.DataFrame(rows).to_csv(buffer, index=False)
        buffer.seek(0)

        assert pd.read_csv(buffer)["patch_label"].tolist() == [7.4]

    def test_a_non_numeric_patch_name_survives_intact(self):
        # A lettered patch is a name, not a number. It reaches the dashboard
        # unchanged rather than raising on the way through.
        rows = build_rows([{"match_id": 1, "patch": 61}], {61: "7.40b"})

        assert round_trip(rows)["patch_label"].tolist() == ["7.40b"]

    def test_the_game_mode_flag_survives_as_a_boolean(self, patch_map):
        rows = build_rows(
            [{"match_id": 1, "game_mode": 2}, {"match_id": 2, "game_mode": 1}],
            patch_map,
        )

        assert round_trip(rows)["non_captains_mode"].tolist() == [False, True]


class TestPatchSortKey:
    def test_orders_patch_labels_by_release(self):
        assert sorted(["7.41", "7.39", "7.40"], key=patch_sort_key) == [
            "7.39",
            "7.40",
            "7.41",
        ]

    def test_orders_a_lettered_patch_after_the_one_it_revises(self):
        assert sorted(["7.41", "7.40b", "7.40"], key=patch_sort_key) == [
            "7.40",
            "7.40b",
            "7.41",
        ]

    def test_sorts_an_unrecognised_label_last(self):
        # "Unknown" is the label for a match with no patch. Sorting it rather
        # than raising is the point: the filter list must still build.
        assert sorted(["Unknown", "7.40"], key=patch_sort_key) == ["7.40", "Unknown"]
