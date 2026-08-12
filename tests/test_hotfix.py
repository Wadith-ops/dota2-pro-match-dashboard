"""
Resolving the hotfix a match was played on.

`patch_label` records the gameplay patch, because that is all OpenDota's
constants carry — 61 entries, every name a plain `d.d`. Valve's own patch list
carries all 117 releases including the lettered ones, and every match already
knows when it started, so the hotfix is derivable.

The rule these tests hold is that **`patch_label` decides the gameplay patch and
Valve's list only chooses the revision within it**. Every one of Valve's 117
timestamps is exactly midnight US/Pacific, so the field is a release date
dressed as a moment; taken literally it puts every match played from midnight
onwards on release day onto a patch the client did not have yet. See ADR-0012.
"""
from datetime import datetime, timezone

import pytest

from core import (
    CSV_DTYPES,
    build_rows,
    carry_forward_hotfix,
    gameplay_patch,
    patch_releases,
    patch_sort_key,
    resolve_hotfix,
)

# The three releases these tests turn on, as unix timestamps.
#
#   7.39e   2025-10-02 07:00 UTC
#   7.40    2025-12-15 08:00 UTC  — midnight Pacific, and the whole problem
#   7.40b   2025-12-23 08:00 UTC
VALVE_7_39E = 1759388400
VALVE_7_40 = 1765785600
VALVE_7_40B = 1766476800

# OpenDota dates 7.40's release at 2025-12-16T00:50:40Z — 16:50 Pacific on the
# 15th, nearly seventeen hours after Valve's timestamp for the same patch. Every
# match in between is a match Valve's list places wrongly.
A_MATCH_ON_RELEASE_DAY_MORNING = 1765810500  # 2025-12-15 14:55 UTC


def ts(text):
    """A UTC timestamp from `YYYY-MM-DD HH:MM`."""
    return int(
        datetime.strptime(text, "%Y-%m-%d %H:%M")
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )


class TestTheReleaseTable:
    def test_valves_list_becomes_an_ascending_table(self, patch_release_table):
        assert len(patch_release_table) == 117
        assert patch_release_table == sorted(patch_release_table)
        assert patch_release_table[0] == (1517472000, "7.08")
        assert patch_release_table[-1] == (1785394800, "7.41e")

    def test_it_carries_the_lettered_releases_opendota_has_no_name_for(
        self, patch_release_table
    ):
        names = [name for _, name in patch_release_table]

        assert "7.40b" in names
        assert "7.40c" in names
        assert "7.41e" in names

    def test_an_entry_missing_either_field_is_dropped_not_guessed_at(self):
        # A release with no timestamp cannot place a match on either side of
        # itself, and a timestamp with no name resolves to nothing worth having.
        table = patch_releases([
            {"patch_name": "7.40", "patch_timestamp": VALVE_7_40},
            {"patch_name": "7.40b"},
            {"patch_timestamp": VALVE_7_40B},
        ])

        assert table == [(VALVE_7_40, "7.40")]

    def test_an_empty_list_is_an_empty_table(self):
        assert patch_releases([]) == []


class TestEveryValveTimestampIsMidnightPacific:
    """
    The fact the whole boundary rule rests on. Valve's `patch_timestamp` looks
    like a release moment and is a release *date*: 07:00 UTC through the summer
    and 08:00 UTC through the winter, which is midnight America/Los_Angeles on
    both sides of daylight saving. A patch that actually shipped at 16:50
    Pacific is recorded as having shipped seventeen hours earlier.
    """

    def test_all_117_releases_land_on_a_pacific_midnight(self, patch_release_table):
        pacific = pytest.importorskip("zoneinfo").ZoneInfo("America/Los_Angeles")

        times = {
            datetime.fromtimestamp(stamp, tz=pacific).strftime("%H:%M:%S")
            for stamp, _ in patch_release_table
        }

        assert times == {"00:00:00"}


class TestTheGameplayPatchDecidesWhichHotfixIsPossible:
    def test_a_match_takes_the_latest_release_within_its_own_patch(
        self, patch_release_table
    ):
        played = ts("2026-02-01 12:00")  # 7.40c released 2026-01-21

        assert resolve_hotfix(played, "7.40", patch_release_table) == "7.40c"

    def test_release_day_stays_on_the_patch_opendota_says_it_was_played_on(
        self, patch_release_table
    ):
        # Valve's list, read literally, calls this match 7.40. OpenDota says
        # 7.39, and OpenDota's boundary is the client update rather than the
        # date on the patch notes.
        assert resolve_hotfix(
            A_MATCH_ON_RELEASE_DAY_MORNING, "7.39", patch_release_table
        ) == "7.39e"

    def test_the_unconstrained_answer_would_have_been_the_newer_patch(
        self, patch_release_table
    ):
        # Stated as its own test so the rule is visible as a choice rather than
        # as a coincidence: without the label, Valve's list does say 7.40 here.
        assert resolve_hotfix(
            A_MATCH_ON_RELEASE_DAY_MORNING, "Unknown", patch_release_table
        ) == "7.40"

    def test_a_patch_with_no_revision_yet_resolves_to_itself(
        self, patch_release_table
    ):
        # OpenDota dates 7.41's release at 2026-03-24T00:50Z, six hours before
        # Valve's midnight-Pacific timestamp for it. A match in between is on
        # 7.41 with nothing lettered released — the answer is the label itself,
        # never the previous patch's last hotfix and never nothing at all.
        assert resolve_hotfix(
            ts("2026-03-24 03:00"), "7.41", patch_release_table
        ) == "7.41"

    def test_an_unrecognisable_label_falls_back_to_valves_list(
        self, patch_release_table
    ):
        # "Unknown" is not a claim about the gameplay patch, so there is nothing
        # to hold the answer inside. Valve's list is then the only evidence
        # there is, and it is better than none.
        assert resolve_hotfix(
            ts("2026-05-10 12:00"), "Unknown", patch_release_table
        ) == "7.41c"


class TestWhenThereIsNothingToResolveFrom:
    def test_no_release_table_resolves_nothing(self):
        # Which is a failed fetch, not a match with no hotfix — see
        # `carry_forward_hotfix`.
        assert resolve_hotfix(VALVE_7_40, "7.40", []) is None

    def test_a_match_with_no_start_time_resolves_nothing(
        self, patch_release_table
    ):
        assert resolve_hotfix(None, "7.40", patch_release_table) is None

    def test_a_match_older_than_every_release_resolves_nothing(
        self, patch_release_table
    ):
        # Valve's list starts at 7.08, February 2018. Nothing before it can be
        # placed, and the honest answer is that this run does not know.
        assert resolve_hotfix(ts("2017-01-01 12:00"), "7.07", patch_release_table) is None


class TestTheBoundaryCostsTwentyEightMatches:
    """
    The number the decision is worth, held so that changing the rule shows up as
    a changed number rather than as a silent reassignment.

    These 28 are every match in the dataset the two sources disagree about —
    DreamLeague Season 27, all of them inside the nine hours between Valve's
    timestamp for 7.40 and the client update OpenDota dates it from. There is no
    second such window: 7.41 released while nobody was playing.
    """

    def test_the_dataset_holds_twenty_eight_disputed_matches(
        self, hotfix_boundary_matches
    ):
        assert len(hotfix_boundary_matches) == 28
        assert {m["patch_label"] for m in hotfix_boundary_matches} == {"7.39"}

    def test_every_one_stays_on_the_patch_it_was_played_on(
        self, hotfix_boundary_matches, patch_release_table
    ):
        resolved = {
            resolve_hotfix(m["start_time"], m["patch_label"], patch_release_table)
            for m in hotfix_boundary_matches
        }

        assert resolved == {"7.39e"}

    def test_reading_valves_timestamp_literally_would_move_all_of_them(
        self, hotfix_boundary_matches, patch_release_table
    ):
        # The rejected rule, kept executable. If this ever stops saying 7.40,
        # Valve has changed what the timestamp means and ADR-0012 needs reading
        # again.
        literal = {
            resolve_hotfix(m["start_time"], "Unknown", patch_release_table)
            for m in hotfix_boundary_matches
        }

        assert literal == {"7.40"}


class TestGameplayPatch:
    def test_a_lettered_name_reduces_to_the_patch_it_revises(self):
        assert gameplay_patch("7.40c") == "7.40"

    def test_an_unrevised_name_is_its_own_gameplay_patch(self):
        assert gameplay_patch("7.40") == "7.40"

    def test_a_name_that_is_not_a_patch_has_no_gameplay_patch(self):
        assert gameplay_patch("Unknown") is None


class TestTheColumnOnTheRow:
    def test_the_row_carries_both_grains(self, patch_map, patch_release_table):
        rows = build_rows(
            [{"match_id": 1, "patch": 59, "start_time": ts("2026-02-01 12:00")}],
            patch_map,
            patch_release_table,
        )

        assert rows[0]["patch_label"] == "7.40"
        assert rows[0]["patch_hotfix"] == "7.40c"

    def test_the_hotfix_never_contradicts_the_label(
        self, patch_map, patch_release_table
    ):
        rows = build_rows(
            [{"match_id": 1, "patch": 58,
              "start_time": A_MATCH_ON_RELEASE_DAY_MORNING}],
            patch_map,
            patch_release_table,
        )

        assert rows[0]["patch_label"] == "7.39"
        assert gameplay_patch(rows[0]["patch_hotfix"]) == "7.39"

    def test_no_release_table_leaves_the_column_null_and_the_row_whole(
        self, patch_map
    ):
        # The default. A caller reading rows for something other than the patch
        # — the coverage audit reads them for team ids — must not have to fetch
        # a patch list to get them.
        rows = build_rows([{"match_id": 1, "patch": 59, "start_time": VALVE_7_40}],
                          patch_map)

        assert rows[0]["patch_hotfix"] is None
        assert rows[0]["patch_label"] == "7.40"


class TestCarryingTheColumnForward:
    """
    The CSV is rebuilt whole every run, so a failed fetch of Valve's patch list
    would write a blank column over a full one. A patch list that cannot be
    fetched costs the newest matches their hotfix, never every match its hotfix.
    """

    def test_an_unresolved_row_keeps_what_was_already_recorded(self):
        rows, carried = carry_forward_hotfix(
            [{"match_id": 1, "patch_hotfix": None}], {1: "7.40c"}
        )

        assert rows[0]["patch_hotfix"] == "7.40c"
        assert carried == 1

    def test_a_resolved_row_is_never_overwritten_by_the_old_value(self):
        # Carrying forward fills; it does not correct. A rule change has to be
        # able to move an answer, and the CSV is not the authority on one.
        rows, carried = carry_forward_hotfix(
            [{"match_id": 1, "patch_hotfix": "7.39e"}], {1: "7.40"}
        )

        assert rows[0]["patch_hotfix"] == "7.39e"
        assert carried == 0

    def test_a_match_with_nothing_recorded_stays_unresolved(self):
        rows, carried = carry_forward_hotfix(
            [{"match_id": 2, "patch_hotfix": None}], {1: "7.40c"}
        )

        assert rows[0]["patch_hotfix"] is None
        assert carried == 0

    def test_nothing_to_carry_forward_leaves_every_row_alone(self):
        rows, carried = carry_forward_hotfix(
            [{"match_id": 1, "patch_hotfix": None},
             {"match_id": 2, "patch_hotfix": "7.41"}], {}
        )

        assert [row["patch_hotfix"] for row in rows] == [None, "7.41"]
        assert carried == 0


class TestTheHotfixAxisNeedsNoNewSortKey:
    """
    `patch_sort_key` already orders a lettered name after the patch it revises —
    it was written that way as forward cover, and this is the cover being
    claimed. Confirmed here rather than reimplemented.
    """

    def test_the_datasets_own_hotfix_buckets_sort_by_release(self):
        buckets = ["7.41c", "7.39e", "7.40", "7.41", "7.40c", "7.41a"]

        assert sorted(buckets, key=patch_sort_key) == [
            "7.39e", "7.40", "7.40c", "7.41", "7.41a", "7.41c",
        ]

    def test_the_column_is_read_back_from_the_csv_as_a_string(self):
        # "7.40" with no letter re-infers as the float 7.4 without this, which
        # is the defect `04` fixed for `patch_label`.
        assert CSV_DTYPES["patch_hotfix"] is str
