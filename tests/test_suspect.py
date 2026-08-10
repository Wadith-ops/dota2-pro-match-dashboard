"""
Tests for suspect-match classification and the re-fetch decision.

A suspect match is one whose objective data is missing or implausible. The
zeros such a match contributes are indistinguishable from a game where nothing
happened, so they are excluded from every average — but the match itself is
kept, flagged, and re-fetched for a while in case OpenDota parses the replay
late. See `tier1-pipeline-automation/05` and ADR-0007.

The clock is a parameter throughout: deciding whether to retry is pure, and
reading the time is the pipeline shell's job.
"""
from datetime import datetime, timedelta, timezone

from core import (
    FETCH_COMPLETE,
    FETCH_HELD,
    FETCH_UNPARSED,
    SUSPECT_NO_KILLS,
    SUSPECT_NO_OBJECTIVES,
    SUSPECT_NO_TOWERS,
    SUSPECT_RETRY_DAYS,
    build_rows,
    classify_fetch,
    coverage_meta,
    flatten_match,
    index_by_match_id,
    retry_window_open,
    store_match,
    suspect_reasons,
)

# A tower kill, so a synthetic match can carry a non-empty objectives array.
TOWER = {"type": "building_kill", "key": "npc_dota_badguys_tower1_mid", "time": 600}

PLAYED_AT = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def match(**overrides):
    """A plausible complete match, before whatever the test wants to break."""
    base = {
        "match_id": 1,
        "duration": 2400,
        "start_time": int(PLAYED_AT.timestamp()),
        "radiant_score": 25,
        "dire_score": 30,
        "objectives": [TOWER],
    }
    base.update(overrides)
    return base


class TestSuspectReasons:
    def test_a_complete_match_is_not_suspect(self, normal_match):
        assert suspect_reasons(normal_match) == ()

    def test_a_match_with_no_objectives_key_is_suspect(self, empty_objectives_match):
        assert "objectives" not in empty_objectives_match

        assert suspect_reasons(empty_objectives_match) == (SUSPECT_NO_OBJECTIVES,)

    def test_an_empty_objectives_array_is_suspect(self):
        assert suspect_reasons(match(objectives=[])) == (SUSPECT_NO_OBJECTIVES,)

    def test_missing_objectives_are_reported_once_not_three_times(self):
        # A match with no objectives array also has no towers in it, and the
        # tower rule would fire too. Reporting both would dress one fact up as
        # corroborating evidence: the zero towers *are* the missing array. The
        # tower rule exists for a match that has objectives and still records
        # no buildings, which is a different and independent signal.
        reasons = suspect_reasons(match(objectives=None, duration=3600))

        assert reasons == (SUSPECT_NO_OBJECTIVES,)

    def test_neither_side_losing_a_tower_in_a_long_game_is_suspect(self):
        # Objectives were recorded — a Roshan kill is in the array — but no
        # building fell in forty minutes, which does not happen in a real game.
        objectives = [{"type": "CHAT_MESSAGE_ROSHAN_KILL", "team": 2, "time": 900}]

        reasons = suspect_reasons(match(duration=2400, objectives=objectives))

        assert reasons == (SUSPECT_NO_TOWERS,)

    def test_one_side_losing_a_tower_is_enough_to_clear_the_tower_rule(self):
        # The criterion is *both* sides at zero. A stomp where the winner never
        # lost a building is an ordinary result, not a data fault.
        assert suspect_reasons(match(duration=3600, objectives=[TOWER])) == ()

    def test_a_short_game_with_no_towers_is_not_suspect(self):
        # Under twenty minutes, no buildings is a plausible early forfeit.
        objectives = [{"type": "CHAT_MESSAGE_FIRSTBLOOD", "time": 90}]

        assert suspect_reasons(match(duration=900, objectives=objectives)) == ()

    def test_the_tower_rule_starts_after_twenty_minutes_exactly(self):
        objectives = [{"type": "CHAT_MESSAGE_FIRSTBLOOD", "time": 90}]

        at_twenty = suspect_reasons(match(duration=1200, objectives=objectives))
        just_after = suspect_reasons(match(duration=1201, objectives=objectives))

        assert at_twenty == ()
        assert just_after == (SUSPECT_NO_TOWERS,)

    def test_an_unrecorded_duration_does_not_trigger_the_tower_rule(self):
        # Without a duration there is no way to tell a long game from a short
        # one, and guessing would flag every match the API answered thinly.
        objectives = [{"type": "CHAT_MESSAGE_FIRSTBLOOD", "time": 90}]

        assert suspect_reasons(match(duration=None, objectives=objectives)) == ()

    def test_a_combined_kill_score_of_zero_is_suspect(self):
        reasons = suspect_reasons(match(radiant_score=0, dire_score=0))

        assert reasons == (SUSPECT_NO_KILLS,)

    def test_a_single_kill_by_either_side_clears_the_kill_rule(self):
        assert suspect_reasons(match(radiant_score=0, dire_score=1)) == ()

    def test_an_unrecorded_score_counts_as_no_kills(self):
        # An absent score is not evidence of a goalless game; it is evidence
        # that the payload is thin, which is the thing being caught.
        reasons = suspect_reasons(match(radiant_score=None, dire_score=None))

        assert reasons == (SUSPECT_NO_KILLS,)

    def test_every_independent_reason_is_reported(self):
        objectives = [{"type": "CHAT_MESSAGE_ROSHAN_KILL", "team": 2, "time": 900}]
        reasons = suspect_reasons(
            match(duration=2400, objectives=objectives, radiant_score=0, dire_score=0)
        )

        assert reasons == (SUSPECT_NO_TOWERS, SUSPECT_NO_KILLS)

    def test_the_objective_counts_can_be_supplied_rather_than_recomputed(
        self, normal_match
    ):
        # `flatten_match` has already counted the objectives by the time it
        # classifies, and passing them in must give the same verdict as letting
        # the classifier count them itself.
        from core import flatten_objectives

        counts = flatten_objectives(normal_match["objectives"])

        assert suspect_reasons(normal_match, counts) == suspect_reasons(normal_match)


class TestTheFiveKnownMatches:
    """The five DreamLeague Season 29 matches named in the issue."""

    def test_all_five_are_flagged_for_missing_objectives(self, suspect_matches):
        flagged = {
            m["match_id"]: suspect_reasons(m) for m in suspect_matches
        }

        assert flagged == {
            8809802771: (SUSPECT_NO_OBJECTIVES,),
            8809797141: (SUSPECT_NO_OBJECTIVES,),
            8809726019: (SUSPECT_NO_OBJECTIVES,),
            8822088303: (SUSPECT_NO_OBJECTIVES,),
            8821954344: (SUSPECT_NO_OBJECTIVES,),
        }

    def test_the_ninety_six_minute_game_reads_as_zero_objectives(
        self, suspect_matches, patch_map
    ):
        # The match that motivated the issue: 96 minutes, 70-60 on kills, and
        # every objective column zero. Nothing about the row itself says the
        # zeros are unknown — only the flag does.
        row = flatten_match(suspect_matches[-1], patch_map)

        assert row["match_id"] == 8821954344
        assert row["duration_mins"] == 96.0
        assert row["radiant_score"] + row["dire_score"] == 130
        assert row["radiant_towers_lost"] == 0
        assert row["dire_towers_lost"] == 0
        assert row["is_suspect"] is True
        assert row["suspect_reason"] == SUSPECT_NO_OBJECTIVES

    def test_they_are_the_only_suspect_matches_among_the_fixtures(
        self, normal_match, negative_first_blood_match, game_mode_1_match
    ):
        # Guards the criteria against catching ordinary matches. A rule that
        # flags a fifth of the dataset is worse than no rule at all.
        for complete in (normal_match, negative_first_blood_match, game_mode_1_match):
            assert suspect_reasons(complete) == ()


class TestFlattenedRow:
    def test_a_complete_match_carries_an_unset_flag_and_no_reason(
        self, normal_match, patch_map
    ):
        row = flatten_match(normal_match, patch_map)

        assert row["is_suspect"] is False
        assert row["suspect_reason"] == ""

    def test_a_suspect_match_carries_its_reasons_as_one_string(self, patch_map):
        objectives = [{"type": "CHAT_MESSAGE_ROSHAN_KILL", "team": 2, "time": 900}]
        row = flatten_match(
            match(duration=2400, objectives=objectives, radiant_score=0, dire_score=0),
            patch_map,
        )

        assert row["is_suspect"] is True
        assert row["suspect_reason"] == f"{SUSPECT_NO_TOWERS};{SUSPECT_NO_KILLS}"

    def test_a_match_that_parses_late_loses_its_flag(self, patch_map):
        # The transition the re-fetch exists for. The same match, fetched twice:
        # first before OpenDota parsed the replay, then after. No manual step
        # sits between the two rows — re-flattening is the whole correction.
        before = flatten_match(match(objectives=None), patch_map)
        after = flatten_match(match(objectives=[TOWER]), patch_map)

        assert before["is_suspect"] is True
        assert after["is_suspect"] is False
        assert after["suspect_reason"] == ""
        assert after["dire_towers_lost"] == 1


class TestRetryWindow:
    def test_a_freshly_played_match_is_still_worth_retrying(self):
        now = PLAYED_AT + timedelta(hours=6)

        assert retry_window_open(match(), now) is True

    def test_the_window_closes_five_days_after_the_match_started(self):
        assert SUSPECT_RETRY_DAYS == 5

        assert retry_window_open(match(), PLAYED_AT + timedelta(days=5)) is False
        assert retry_window_open(match(), PLAYED_AT + timedelta(days=4, hours=23)) is True

    def test_a_match_with_no_start_time_is_never_retried(self):
        # Without a start there is no deadline, and a match that can never age
        # out would be re-fetched on every run forever.
        assert retry_window_open(match(start_time=None), PLAYED_AT) is False

    def test_a_backfilled_event_is_past_its_deadline_on_arrival(self):
        # The anchor is the match's start, not when the pipeline first saw it.
        # An event imported two months late gets no retries, which is right:
        # its replays were parsed long ago or never.
        played_in_june = int(datetime(2026, 6, 9, tzinfo=timezone.utc).timestamp())

        assert retry_window_open(match(start_time=played_in_june), PLAYED_AT) is False

    def test_the_five_known_matches_are_long_past_retrying(self, suspect_matches):
        # They were played in June and are still unparsed in August. A replay
        # OpenDota has not parsed in two months is not going to be parsed, and
        # re-fetching it every six hours forever costs calls for nothing. The
        # pipeline holds back a match only when it is suspect *and* inside its
        # window; these are suspect and outside it, so they stay checkpointed.
        now = datetime(2026, 8, 10, tzinfo=timezone.utc)

        assert all(suspect_reasons(m) for m in suspect_matches)
        assert [retry_window_open(m, now) for m in suspect_matches] == [False] * 5


class TestClassifyFetch:
    """
    The three-way verdict the pipeline acts on. `FETCH_HELD` is the one that
    matters: it means "do not check this off", and the missing checkpoint entry
    is what makes the next run fetch the match again.
    """

    def test_a_complete_match_is_finished_with(self, normal_match):
        assert classify_fetch(normal_match, PLAYED_AT) == FETCH_COMPLETE

    def test_a_fresh_suspect_match_is_held_for_another_attempt(self):
        six_hours_later = PLAYED_AT + timedelta(hours=6)

        assert classify_fetch(match(objectives=None), six_hours_later) == FETCH_HELD

    def test_a_suspect_match_out_of_time_is_given_up_on(self):
        assert classify_fetch(match(objectives=None),
                              PLAYED_AT + timedelta(days=6)) == FETCH_UNPARSED

    def test_the_verdict_turns_over_exactly_at_five_days(self):
        assert classify_fetch(match(objectives=None),
                              PLAYED_AT + timedelta(days=4, hours=23)) == FETCH_HELD
        assert classify_fetch(match(objectives=None),
                              PLAYED_AT + timedelta(days=5)) == FETCH_UNPARSED

    def test_a_match_that_parsed_late_is_complete_on_the_second_look(self):
        # The two fetches of one held match, in order. The second answer is what
        # stops the retry: nothing else has to notice that the match was fixed.
        six_hours_later = PLAYED_AT + timedelta(hours=6)

        assert classify_fetch(match(objectives=None), six_hours_later) == FETCH_HELD
        assert classify_fetch(match(objectives=[TOWER]), six_hours_later) == FETCH_COMPLETE

    def test_an_old_match_with_complete_data_is_never_held(self):
        # Age alone decides nothing — only a suspect match is ever held back.
        assert classify_fetch(match(), PLAYED_AT + timedelta(days=60)) == FETCH_COMPLETE

    def test_the_five_known_matches_are_given_up_on_not_held(self, suspect_matches):
        now = datetime(2026, 8, 10, tzinfo=timezone.utc)

        assert [classify_fetch(m, now) for m in suspect_matches] == [FETCH_UNPARSED] * 5


class TestStoringARefetch:
    def test_a_new_match_is_appended(self):
        matches = [match(match_id=1)]
        positions = index_by_match_id(matches)

        store_match(matches, positions, match(match_id=2))

        assert [m["match_id"] for m in matches] == [1, 2]
        assert positions == {1: 0, 2: 1}

    def test_a_refetched_match_replaces_its_earlier_payload(self):
        # The correction path end to end: the first fetch caught the match
        # before the replay was parsed, the second after. Two copies would
        # count the match twice in every average — the exact fault this issue
        # exists to remove, arriving through the re-fetch meant to fix it.
        unparsed = match(match_id=1, objectives=None)
        matches = [unparsed, match(match_id=2)]
        positions = index_by_match_id(matches)

        store_match(matches, positions, match(match_id=1, objectives=[TOWER]))

        assert len(matches) == 2
        assert matches[0]["objectives"] == [TOWER]
        assert positions == {1: 0, 2: 1}

    def test_rows_built_after_a_refetch_hold_one_row_and_no_flag(self, patch_map):
        matches = [match(match_id=1, objectives=None)]
        positions = index_by_match_id(matches)

        store_match(matches, positions, match(match_id=1, objectives=[TOWER]))
        rows = build_rows(matches, patch_map)

        assert len(rows) == 1
        assert rows[0]["is_suspect"] is False

    def test_a_payload_with_no_match_id_is_left_out_of_the_index(self):
        assert index_by_match_id([{"duration": 100}, match(match_id=7)]) == {7: 1}


class TestExcludedCount:
    def test_coverage_meta_counts_the_suspect_rows(self, patch_map):
        rows = [
            flatten_match(match(match_id=1), patch_map),
            flatten_match(match(match_id=2, objectives=None), patch_map),
            flatten_match(match(match_id=3, objectives=[]), patch_map),
        ]

        meta = coverage_meta(rows, datetime(2026, 8, 10, tzinfo=timezone.utc))

        assert meta["match_count"] == 3
        assert meta["excluded_count"] == 2

    def test_a_clean_dataset_reports_zero_excluded_not_null(self, patch_map):
        # Null meant "not yet computed". Now that it is computed, zero is a
        # real answer and the dashboard is entitled to say so.
        meta = coverage_meta(
            [flatten_match(match(), patch_map)],
            datetime(2026, 8, 10, tzinfo=timezone.utc),
        )

        assert meta["excluded_count"] == 0
