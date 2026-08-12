"""
Resolving Liquipedia Tier 1 events to OpenDota leagues.

The two sources name the same tournament differently and always will — "BLAST
SLAM VII" against "Blast Slam VII", "PGL Wallachia Season 7" against "PGL
Wallachia 2026 Season 7". **Names are never compared.** A league resolves to an
event when every one of its matches falls inside that event's published window,
and where a window holds more than one such league the one with the highest
share of matches involving teams already in the dataset wins. See ADR-0001 and
`tier1-pipeline-automation/08`.

Everything here is pure: events and league summaries in, resolutions out.
Fetching the match lists is the pipeline shell's job.

The centrepiece is `TestThe2026Season`, which runs the whole 2026 Tier 1 list
against every OpenDota league that played a match that year and asserts the
answer league by league. That is the acceptance criterion, and it is recorded
data rather than a constructed example precisely so it cannot drift into
agreeing with the code.
"""
from datetime import date, datetime, timezone

import pytest

from core import (
    LEDGER_ACTIVE,
    LEDGER_PENDING,
    LEDGER_REJECTED,
    NON_COMPETITIVE_TIERS,
    RESOLVER_GRACE_DAYS,
    apply_resolutions,
    events_awaiting_resolution,
    events_in_year,
    known_team_ids,
    league_catalogue,
    parse_tier1_events,
    resolution_problems,
    resolution_report,
    resolution_walk_start,
    resolve_tier1_events,
    summarise_leagues,
    team_overlap,
    tier1_resolution_state,
    window_coverage,
)


def match(league_id, day, radiant=None, dire=None):
    """
    One league match row, as OpenDota's league and pro match lists return it.

    Played at midday UTC, so no assertion here turns on which side of midnight
    a timezone conversion lands.
    """
    noon = datetime.fromisoformat(f"{day}T12:00:00+00:00")
    return {
        "leagueid"       : league_id,
        "start_time"     : int(noon.timestamp()),
        "radiant_team_id": radiant,
        "dire_team_id"   : dire,
    }


def event(name, start, end):
    return {"name": name, "start_date": start, "end_date": end}


class TestSummarisingALeague:
    def test_the_window_is_the_first_and_last_match_dates(self):
        summaries = summarise_leagues([
            match(19099, "2026-02-05"),
            match(19099, "2026-02-03"),
            match(19099, "2026-02-15"),
        ])

        assert summaries[19099]["first_date"] == "2026-02-03"
        assert summaries[19099]["last_date"] == "2026-02-15"
        assert summaries[19099]["match_count"] == 3

    def test_each_match_contributes_two_team_slots(self):
        # Two per match, not one per distinct team. The overlap share is a
        # share of appearances, so a team playing ten matches counts ten times.
        summaries = summarise_leagues([
            match(19099, "2026-02-05", radiant=15, dire=39),
            match(19099, "2026-02-06", radiant=15, dire=8291895),
        ])

        assert summaries[19099]["team_slots"] == [15, 39, 15, 8291895]

    def test_an_anonymous_team_still_occupies_a_slot(self):
        # A null team id is a team we cannot identify, not an absent one. It
        # belongs in the denominator: a league of unidentifiable teams is
        # exactly the league that should score low.
        summaries = summarise_leagues([match(19099, "2026-02-05", radiant=15)])

        assert summaries[19099]["team_slots"] == [15, None]

    def test_matches_are_grouped_by_league(self):
        summaries = summarise_leagues([
            match(19099, "2026-02-05"),
            match(19290, "2026-02-06"),
        ])

        assert sorted(summaries) == [19099, 19290]

    def test_a_match_with_no_start_time_is_ignored(self):
        # It cannot be placed in any window, and letting it through would put a
        # None into the min/max that decides one.
        summaries = summarise_leagues([
            match(19099, "2026-02-05"),
            {"leagueid": 19099, "start_time": None},
        ])

        assert summaries[19099]["match_count"] == 1

    def test_the_catalogue_supplies_the_name_and_tier(self):
        catalogue = league_catalogue([
            {"leagueid": 19099, "name": "BLAST SLAM VI", "tier": "professional"},
        ])

        summaries = summarise_leagues([match(19099, "2026-02-05")], catalogue)

        assert summaries[19099]["name"] == "BLAST SLAM VI"
        assert summaries[19099]["tier"] == "professional"

    def test_a_league_the_catalogue_does_not_list_still_summarises(self):
        # OpenDota's league list and its match lists are two endpoints and can
        # disagree. Dropping the league would be a silent exclusion.
        summaries = summarise_leagues([match(19099, "2026-02-05")], {})

        assert summaries[19099]["name"] is None
        assert summaries[19099]["tier"] is None


class TestTeamOverlap:
    def test_it_is_the_share_of_slots_held_by_teams_already_tracked(self):
        summary = summarise_leagues([
            match(1, "2026-02-05", radiant=15, dire=39),
            match(1, "2026-02-06", radiant=15, dire=999),
        ])[1]

        assert team_overlap(summary, {15, 39}) == 0.75

    def test_a_league_of_strangers_scores_zero(self):
        summary = summarise_leagues([match(1, "2026-02-05", radiant=7, dire=8)])[1]

        assert team_overlap(summary, {15, 39}) == 0.0

    def test_a_league_with_no_matches_scores_zero_rather_than_dividing_by_zero(self):
        assert team_overlap({"team_slots": []}, {15}) == 0.0

    def test_known_team_ids_reads_both_sides_of_every_row(self):
        rows = [
            {"radiant_team_id": 15, "dire_team_id": 39},
            {"radiant_team_id": 15, "dire_team_id": None},
        ]

        assert known_team_ids(rows) == {15, 39}


class TestTheDateWindow:
    """
    A candidate matches when *every* match it holds falls inside the event's
    window. A league whose matches spill outside is a different tournament that
    happens to overlap — which is exactly how qualifiers and Division 2 circuits
    exclude themselves, with no rule naming them.
    """

    EVENT = event("BLAST SLAM VI", "2026-02-03", "2026-02-15")

    def resolve(self, *summaries_matches, known=frozenset()):
        summaries = summarise_leagues([m for rows in summaries_matches for m in rows])
        return resolve_tier1_events([self.EVENT], summaries, known)[0]

    def test_a_league_inside_the_window_matches(self):
        resolution = self.resolve([match(19099, "2026-02-04"),
                                   match(19099, "2026-02-14")])

        assert resolution["league_id"] == 19099

    def test_a_league_running_past_the_window_does_not(self):
        resolution = self.resolve([match(19099, "2026-02-04"),
                                   match(19099, "2026-02-25")])

        assert resolution["league_id"] is None

    def test_a_league_starting_before_the_window_does_not(self):
        resolution = self.resolve([match(19099, "2026-01-20"),
                                   match(19099, "2026-02-14")])

        assert resolution["league_id"] is None

    def test_two_days_of_grace_are_allowed_at_each_end(self):
        # Never yet needed — OpenDota's first and last match dates matched
        # Liquipedia's published dates exactly in all nine played 2026 events.
        # It is here for the day a final runs past midnight in the venue's
        # timezone, not for a case that is known.
        assert RESOLVER_GRACE_DAYS == 2

        resolution = self.resolve([match(19099, "2026-02-01"),
                                   match(19099, "2026-02-17")])

        assert resolution["league_id"] == 19099

    def test_the_third_day_is_outside(self):
        resolution = self.resolve([match(19099, "2026-01-31"),
                                   match(19099, "2026-02-17")])

        assert resolution["league_id"] is None

    def test_an_event_with_no_published_window_resolves_to_nothing(self):
        # Liquipedia writes "TBD" for an event it has not scheduled. One
        # unscheduled event must not take a candidate at random.
        summaries = summarise_leagues([match(19099, "2026-02-04")])

        resolution = resolve_tier1_events(
            [event("BLAST SLAM X", None, None)], summaries, set()
        )[0]

        assert resolution["league_id"] is None
        assert resolution["candidates"] == []


class TestNestedEvents:
    """
    Tier 1 events overlap each other, and a league nested inside a longer
    window is a candidate for both.

    October 2025 is the real case. BLAST Slam IV ran 14 October to 9 November;
    FISSURE PLAYGROUND 2 ran 23 October to 2 November, entirely inside it. Both
    leagues are made of teams already tracked, so both score 100% for BLAST Slam
    IV and team overlap cannot part them. Ranking on match count picks FISSURE
    PLAYGROUND 2 — it played 124 games to BLAST Slam IV's 96, in half the days —
    and the wrong mapping then evicts the right one, because a league can only
    be one event.
    """

    SLAM_IV  = event("BLAST Slam IV", "2025-10-14", "2025-11-09")
    NESTED   = event("FISSURE PLAYGROUND 2", "2025-10-23", "2025-11-02")

    # 96 matches over the whole window against 124 over the middle of it.
    SUMMARIES = summarise_leagues(
        [match(17419, "2025-10-14", radiant=15, dire=39)] * 48
        + [match(17419, "2025-11-09", radiant=15, dire=39)] * 48
        + [match(18863, "2025-10-23", radiant=15, dire=39)] * 62
        + [match(18863, "2025-11-02", radiant=15, dire=39)] * 62
    )

    def resolve(self):
        return {
            resolution["event"]: resolution
            for resolution in resolve_tier1_events(
                [self.SLAM_IV, self.NESTED], self.SUMMARIES, {15, 39}
            )
        }

    def test_the_league_that_ran_the_window_wins_it(self):
        resolution = self.resolve()["BLAST Slam IV"]

        assert resolution["league_id"] == 17419
        assert resolution["ambiguous"] is True

    def test_the_nested_league_wins_its_own_window(self):
        # And so the two events resolve to two leagues. Coverage is what makes
        # that possible: on match count both windows go to 18863.
        assert self.resolve()["FISSURE PLAYGROUND 2"]["league_id"] == 18863

    def test_coverage_is_reported_beside_the_overlap_that_could_not_decide(self):
        candidates = self.resolve()["BLAST Slam IV"]["candidates"]

        assert [c["overlap"] for c in candidates] == [1.0, 1.0]
        assert candidates[0]["coverage"] == 1.0
        assert candidates[1]["coverage"] < 0.5

    def test_two_events_claiming_one_league_is_reported_not_resolved(self):
        # `apply_resolutions` keys its writes by league id, so a second claim
        # overwrites the first and one event reads as a gap with no reason
        # given. It has to be said out loud instead.
        both_claim_18863 = [
            {"event": "BLAST Slam IV", "league_id": 18863,
             "league_name": "FISSURE PLAYGROUND 2", "overlap": 1.0,
             "ambiguous": True, "candidates": []},
            {"event": "FISSURE PLAYGROUND 2", "league_id": 18863,
             "league_name": "FISSURE PLAYGROUND 2", "overlap": 1.0,
             "ambiguous": False, "candidates": []},
        ]
        ledger = {"leagues": [{"league_id": 18863, "name": "FISSURE PLAYGROUND 2",
                               "tier1_event": None, "verdict": LEDGER_ACTIVE}]}

        problems = resolution_problems(ledger, both_claim_18863)

        assert len(problems) == 1
        assert "claimed by 2 events" in problems[0]


class TestWindowCoverage:
    EVENT = ("2026-02-03", "2026-02-15")  # thirteen days

    def coverage(self, first, last):
        summary = {"first_date": first, "last_date": last}
        return window_coverage(summary, *self.EVENT)

    def test_a_league_spanning_the_whole_window_scores_one(self):
        assert self.coverage("2026-02-03", "2026-02-15") == 1.0

    def test_a_single_day_scores_a_thirteenth(self):
        assert self.coverage("2026-02-08", "2026-02-08") == pytest.approx(1 / 13)

    def test_days_outside_the_window_do_not_count_as_more_of_it(self):
        # The grace lets a league begin two days early. Those two days are not
        # more of the event than the event has.
        assert self.coverage("2026-02-01", "2026-02-15") == 1.0

    def test_an_event_with_no_window_scores_zero_rather_than_raising(self):
        assert window_coverage({"first_date": "2026-02-03",
                                "last_date": "2026-02-15"}, None, None) == 0.0

    def test_it_ranks_but_never_gates(self):
        # A tournament resolves on the day of its first match, covering one day
        # of a twelve-day window. A minimum score here would trade a wrong
        # answer for no answer at all.
        opening_day = summarise_leagues([match(19719, "2026-08-13", radiant=15)])

        resolution = resolve_tier1_events(
            [event("The International 2026", "2026-08-13", "2026-08-23")],
            opening_day,
            {15},
        )[0]

        assert resolution["league_id"] == 19719
        assert resolution["candidates"][0]["coverage"] < 0.1


class TestTheTiebreaker:
    EVENT = event("BLAST SLAM VI", "2026-02-03", "2026-02-15")

    def test_the_highest_overlap_wins(self):
        summaries = summarise_leagues([
            match(19099, "2026-02-04", radiant=15, dire=39),
            match(19290, "2026-02-04", radiant=15, dire=999),
        ])

        resolution = resolve_tier1_events([self.EVENT], summaries, {15, 39})[0]

        assert resolution["league_id"] == 19099
        assert resolution["ambiguous"] is True

    def test_every_candidate_is_recorded_ranked_with_its_score(self):
        # Ambiguity is surfaced, never silently resolved: the losing candidate
        # is in the record so a wrong mapping can be spotted before it enters
        # the dataset.
        summaries = summarise_leagues([
            match(19099, "2026-02-04", radiant=15, dire=39),
            match(19290, "2026-02-04", radiant=15, dire=999),
        ])

        resolution = resolve_tier1_events([self.EVENT], summaries, {15, 39})[0]

        assert [c["league_id"] for c in resolution["candidates"]] == [19099, 19290]
        assert [c["overlap"] for c in resolution["candidates"]] == [1.0, 0.5]

    def test_a_single_candidate_is_not_ambiguous(self):
        summaries = summarise_leagues([match(19099, "2026-02-04", radiant=15)])

        resolution = resolve_tier1_events([self.EVENT], summaries, {15})[0]

        assert resolution["ambiguous"] is False
        assert resolution["candidates"] == [{
            "league_id"  : 19099,
            "name"       : None,
            "overlap"    : 0.5,
            "coverage"   : round(1 / 13, 4),
            "match_count": 1,
        }]

    def test_there_is_no_minimum_score_to_win(self):
        # An absolute threshold is wrong: the correct winner scored 85.8% in
        # one real window and 74.5% in another. Ranking is relative.
        summaries = summarise_leagues([match(19099, "2026-02-04", radiant=7, dire=8)])

        resolution = resolve_tier1_events([self.EVENT], summaries, {15, 39})[0]

        assert resolution["league_id"] == 19099
        assert resolution["overlap"] == 0.0

    def test_a_tie_on_overlap_and_coverage_is_broken_by_match_count(self):
        # Two candidates scoring identically still have to resolve the same way
        # on every run, or the ledger churns and the report is not reviewable.
        summaries = summarise_leagues([
            match(19290, "2026-02-04", radiant=15, dire=39),
            match(19290, "2026-02-05", radiant=15, dire=39),
            match(19099, "2026-02-04", radiant=15, dire=39),
            match(19099, "2026-02-05", radiant=15, dire=39),
            match(19099, "2026-02-05", radiant=15, dire=39),
        ])

        resolution = resolve_tier1_events([self.EVENT], summaries, {15, 39})[0]

        assert resolution["league_id"] == 19099

    def test_a_tie_on_everything_is_broken_by_league_id(self):
        summaries = summarise_leagues([
            match(19290, "2026-02-04", radiant=15, dire=39),
            match(19099, "2026-02-04", radiant=15, dire=39),
        ])

        resolution = resolve_tier1_events([self.EVENT], summaries, {15, 39})[0]

        assert resolution["league_id"] == 19099


class TestNonCompetitiveLeagues:
    """
    OpenDota's `tier` cannot *select* Tier 1 — every league this project tracks
    is `professional`, alongside 2,468 others (ADR-0001). But `excluded` is the
    source's own statement that a league is not a competitive event at all, and
    a streamer showmatch series running inside a Tier 1 window is a candidate
    nobody wants to review. It prunes the pool; it never picks the winner.
    """

    EVENT = event("DreamLeague Season 29", "2026-05-13", "2026-05-24")

    def resolve(self, tier):
        catalogue = league_catalogue([
            {"leagueid": 19696, "name": "DreamLeague Season 29",
             "tier": "professional"},
            {"leagueid": 19742, "name": "BetBoom Streamers Battle 13", "tier": tier},
        ])
        summaries = summarise_leagues([
            match(19696, "2026-05-14", radiant=15, dire=39),
            match(19742, "2026-05-19", radiant=15, dire=39),
        ], catalogue)

        return resolve_tier1_events([self.EVENT], summaries, {15, 39})[0]

    def test_an_excluded_league_is_not_a_candidate(self):
        assert self.resolve("excluded")["ambiguous"] is False

    def test_a_professional_league_is(self):
        assert self.resolve("professional")["ambiguous"] is True

    def test_a_league_with_no_tier_at_all_stays_a_candidate(self):
        # The pool is a denylist, not an allowlist. Over-including produces an
        # ambiguity that gets reviewed; under-including produces a silent gap,
        # which is the failure this whole feature exists to end.
        assert self.resolve(None)["ambiguous"] is True

    def test_the_denied_tiers_are_named_rather_than_inferred(self):
        assert NON_COMPETITIVE_TIERS == frozenset({"excluded", "amateur"})


class TestAGapIsNotAnError:
    def test_an_event_with_no_candidate_resolves_to_nothing(self):
        resolution = resolve_tier1_events(
            [event("The International 2026", "2026-08-13", "2026-08-23")], {}, set()
        )[0]

        assert resolution["league_id"] is None
        assert resolution["league_name"] is None
        assert resolution["overlap"] is None
        assert resolution["candidates"] == []

    def test_one_gap_does_not_stop_the_events_around_it(self):
        summaries = summarise_leagues([match(19099, "2026-02-04", radiant=15)])

        resolutions = resolve_tier1_events(
            [
                event("The International 2026", "2026-08-13", "2026-08-23"),
                event("BLAST SLAM VI", "2026-02-03", "2026-02-15"),
            ],
            summaries,
            {15},
        )

        assert [r["league_id"] for r in resolutions] == [None, 19099]

    def test_events_come_back_in_the_order_they_were_given(self):
        names = ["BLAST SLAM VI", "DreamLeague Season 28"]
        resolutions = resolve_tier1_events(
            [event(name, "2026-02-03", "2026-02-15") for name in names], {}, set()
        )

        assert [r["event"] for r in resolutions] == names


class TestWritingTheAnswerIntoTheLedger:
    LEDGER = {"leagues": [
        {"league_id": 19099, "name": "BLAST Slam VI", "tier1_event": None,
         "verdict": LEDGER_ACTIVE},
        {"league_id": 19290, "name": "DreamLeague Division 2 Season 3",
         "tier1_event": None, "verdict": LEDGER_REJECTED},
    ]}

    def resolution(self, league_id, event_name="BLAST SLAM VI"):
        return {"event": event_name, "league_id": league_id, "league_name": None,
                "overlap": 1.0, "ambiguous": False, "candidates": []}

    def test_the_winner_gains_the_event_name(self):
        ledger, changes = apply_resolutions(self.LEDGER, [self.resolution(19099)])

        assert ledger["leagues"][0]["tier1_event"] == "BLAST SLAM VI"
        assert changes == [(19099, None, "BLAST SLAM VI")]

    def test_the_loser_is_left_alone(self):
        ledger, _ = apply_resolutions(self.LEDGER, [self.resolution(19099)])

        assert ledger["leagues"][1]["tier1_event"] is None

    def test_the_verdict_is_never_touched(self):
        # Resolving is discovery; the verdict is Wade's, taken by merging the
        # pull request (`tier1-pipeline-automation/15`). A resolver that set
        # `active` itself would be fetching leagues nobody approved.
        ledger, _ = apply_resolutions(self.LEDGER, [self.resolution(19290)])

        assert ledger["leagues"][1]["verdict"] == LEDGER_REJECTED

    def test_re_resolving_to_the_same_event_changes_nothing(self):
        # The file is 10,000 lines and this runs daily.
        once, _ = apply_resolutions(self.LEDGER, [self.resolution(19099)])
        twice, changes = apply_resolutions(once, [self.resolution(19099)])

        assert twice == once
        assert changes == []

    def test_a_gap_writes_nothing(self):
        ledger, changes = apply_resolutions(self.LEDGER, [self.resolution(None)])

        assert ledger == self.LEDGER
        assert changes == []

    def test_the_original_ledger_is_not_mutated(self):
        apply_resolutions(self.LEDGER, [self.resolution(19099)])

        assert self.LEDGER["leagues"][0]["tier1_event"] is None


class TestWhatTheRunHasToSayOutLoud:
    """
    A resolved league this project is not fetching is the whole point of the
    feature: it is the Esports World Cup, found by the pipeline rather than by a
    friend asking why a team is missing.
    """

    def resolution(self, league_id, event_name="Esports World Cup 2026"):
        return {"event": event_name, "league_id": league_id,
                "league_name": "Esports World Cup 2026", "overlap": 0.745,
                "ambiguous": False, "candidates": []}

    def test_a_resolved_league_awaiting_a_verdict_is_reported(self):
        ledger = {"leagues": [{"league_id": 19785, "name": "Esports World Cup 2026",
                               "tier1_event": None, "verdict": LEDGER_PENDING}]}

        problems = resolution_problems(ledger, [self.resolution(19785)])

        assert len(problems) == 1
        assert "19785" in problems[0]

    def test_a_resolved_league_already_active_is_not(self):
        ledger = {"leagues": [{"league_id": 19785, "name": "Esports World Cup 2026",
                               "tier1_event": None, "verdict": LEDGER_ACTIVE}]}

        assert resolution_problems(ledger, [self.resolution(19785)]) == []

    def test_a_resolved_league_already_rejected_is_not_proposed_again(self):
        # "As Wade, I want to reject a tournament and have that recorded, so
        # that it is never proposed again." The Tier 1 events this project
        # starts *after* are exactly this case — The International 2025 is on
        # Liquipedia's list and resolves cleanly — and re-raising them every
        # morning would be the noise the ledger exists to end. They stay
        # visible, with their verdict, in the resolution record.
        ledger = {"leagues": [{"league_id": 18375, "name": "Esports World Cup 2025",
                               "tier1_event": None, "verdict": LEDGER_REJECTED}]}

        assert resolution_problems(ledger, [self.resolution(18375)]) == []

    def test_a_resolved_league_the_ledger_has_never_heard_of_is_reported(self):
        assert len(resolution_problems({"leagues": []}, [self.resolution(19785)])) == 1

    def test_a_gap_is_not_a_problem_here(self):
        # It is a gap, recorded as one. Reporting it as a ledger problem would
        # make an unstarted tournament look like a fault every run.
        assert resolution_problems({"leagues": []}, [self.resolution(None)]) == []


class TestTheResolutionRecord:
    """
    `data/tier1_resolution.json` — what the dashboard's Upcoming tab and the
    approval pull request read. It is built from the ledger, not from this run's
    candidates, so an event resolved months ago still reads as covered on a run
    that never looked at it.
    """

    EVENTS = [
        event("The International 2026", "2026-08-13", "2026-08-23"),
        event("1win Essence II", "2026-07-30", "2026-08-05"),
    ]
    LEDGER = {"leagues": [
        {"league_id": 20009, "name": "1win Essence II",
         "tier1_event": "1win Essence II", "verdict": LEDGER_ACTIVE},
    ]}

    def test_an_event_the_ledger_maps_reads_as_covered(self):
        records = tier1_resolution_state(self.EVENTS, self.LEDGER)

        assert records[1]["league_id"] == 20009
        assert records[1]["verdict"] == LEDGER_ACTIVE

    def test_an_event_the_ledger_does_not_map_is_a_gap(self):
        records = tier1_resolution_state(self.EVENTS, self.LEDGER)

        assert records[0]["league_id"] is None
        assert records[0]["verdict"] is None

    def test_a_run_that_examined_nothing_still_reports_what_is_covered(self):
        # The ledger is the durable record. A daily run walks back only as far
        # as its unresolved events, so most events are not looked at — and must
        # not regress to gaps because of it.
        records = tier1_resolution_state(self.EVENTS, self.LEDGER, resolutions=[])

        assert records[1]["league_id"] == 20009

    def test_a_previous_runs_candidates_survive_a_run_that_examined_nothing(self):
        # An event is examined exactly once — the run after it resolves has
        # nothing to do. Blanking the ranking then would erase the evidence a
        # contested window was recorded for, a day after recording it and
        # before anyone read the pull request.
        previous = [{"event": "1win Essence II", "ambiguous": True,
                     "candidates": [{"league_id": 20009, "name": "1win Essence II",
                                     "overlap": 0.8583, "match_count": 60}]}]

        records = tier1_resolution_state(
            self.EVENTS, self.LEDGER, resolutions=[], previous=previous
        )

        assert records[1]["ambiguous"] is True
        assert len(records[1]["candidates"]) == 1

    def test_this_runs_ranking_wins_over_the_one_it_remembers(self):
        previous = [{"event": "1win Essence II", "ambiguous": True,
                     "candidates": [{"league_id": 19917, "name": "wrong",
                                     "overlap": 0.1, "match_count": 1}]}]
        resolutions = [{"event": "1win Essence II", "league_id": 20009,
                        "league_name": "1win Essence II", "overlap": 0.8583,
                        "ambiguous": False, "candidates": []}]

        records = tier1_resolution_state(
            self.EVENTS, self.LEDGER, resolutions, previous
        )

        assert records[1]["ambiguous"] is False
        assert records[1]["candidates"] == []

    def test_this_runs_candidates_are_attached_to_the_event_they_belong_to(self):
        resolutions = [{
            "event": "1win Essence II", "league_id": 20009,
            "league_name": "1win Essence II", "overlap": 0.8583, "ambiguous": True,
            "candidates": [{"league_id": 20009, "name": "1win Essence II",
                            "overlap": 0.8583, "match_count": 60},
                           {"league_id": 19917, "name": "The Games of the Future 2026",
                            "overlap": 0.0903, "match_count": 72}],
        }]

        records = tier1_resolution_state(self.EVENTS, self.LEDGER, resolutions)

        assert records[1]["ambiguous"] is True
        assert len(records[1]["candidates"]) == 2
        assert records[0]["candidates"] == []

    def test_the_report_carries_the_clock_it_was_handed(self):
        report = resolution_report(
            tier1_resolution_state(self.EVENTS, self.LEDGER),
            datetime(2026, 8, 10, 21, 30, tzinfo=timezone.utc),
        )

        assert report["generated_at"] == "2026-08-10T21:30:00+00:00"
        assert report["grace_days"] == RESOLVER_GRACE_DAYS
        assert report["gap_count"] == 1
        assert len(report["events"]) == 2


class TestWhichEventsAreStillWorthLooking:
    """
    Deciding how far back to walk OpenDota's pro match list. An event already
    mapped in the ledger costs nothing to skip, and an event that has not
    started has no matches to find.
    """

    EVENTS = [
        event("BLAST SLAM VIII", "2026-09-29", "2026-10-11"),
        event("The International 2026", "2026-08-13", "2026-08-23"),
        event("1win Essence II", "2026-07-30", "2026-08-05"),
    ]
    LEDGER = {"leagues": [
        {"league_id": 20009, "name": "1win Essence II",
         "tier1_event": "1win Essence II", "verdict": LEDGER_ACTIVE},
    ]}

    def awaiting(self, today):
        return [e["name"] for e in events_awaiting_resolution(
            self.EVENTS, self.LEDGER, date.fromisoformat(today)
        )]

    def test_an_event_that_has_not_started_is_not_awaiting_resolution(self):
        assert self.awaiting("2026-08-10") == []

    def test_an_event_under_way_is(self):
        # Day one, not the day after the final. A tournament's data should
        # appear within hours of its first match.
        assert self.awaiting("2026-08-13") == ["The International 2026"]

    def test_an_event_the_ledger_already_maps_is_not(self):
        assert "1win Essence II" not in self.awaiting("2026-10-01")

    def test_an_event_with_no_window_is_not(self):
        awaiting = events_awaiting_resolution(
            [event("BLAST SLAM X", None, None)], {}, date(2026, 8, 13)
        )

        assert awaiting == []

    def test_an_event_older_than_the_lookback_is_not(self):
        # Liquipedia's list reaches back to 2005 and this project has never
        # mapped most of it. Without the horizon the first run walks twenty
        # years of pro matches — and the run after it walks back to the oldest
        # thing it still could not resolve, every day, forever. Auditing the
        # back catalogue is a deliberate separate pass — `audit_coverage.py`,
        # which takes its period on the command line and is not scheduled.
        awaiting = events_awaiting_resolution(
            [event("The International 2013", "2013-08-02", "2013-08-11")],
            {},
            date(2026, 8, 13),
        )

        assert awaiting == []

    def test_the_lookback_is_a_year_and_is_measured_from_today(self):
        old = [event("Slam IV", "2025-08-20", "2025-08-31")]

        assert len(events_awaiting_resolution(old, {}, date(2026, 8, 13))) == 1
        assert events_awaiting_resolution(old, {}, date(2026, 8, 21)) == []


class TestHowFarBackTheWalkGoes:
    """
    `resolution_walk_start` decides how many pro match pages a run spends. It is
    arithmetic over plain data, so it lives in the core rather than beside the
    walk it bounds.
    """

    def since(self, *start_dates):
        return resolution_walk_start(
            [{"name": "e", "start_date": day, "end_date": day} for day in start_dates]
        )

    def midnight(self, day):
        return int(datetime.fromisoformat(f"{day}T00:00:00+00:00").timestamp())

    def test_it_reaches_the_earliest_event_awaiting_resolution(self):
        assert self.since("2026-07-30", "2026-08-05") == self.midnight("2026-07-28")

    def test_it_allows_the_same_grace_the_window_does(self):
        # Otherwise a league whose first match is two days early — the case the
        # grace exists for — is outside the walk that was meant to find it.
        assert self.midnight("2026-07-30") - self.since("2026-07-30") == (
            RESOLVER_GRACE_DAYS * 86400
        )

    def test_it_starts_at_midnight_utc_rather_than_at_the_hour_it_ran(self):
        # A window is published in whole days, so the walk is bounded in whole
        # days. Whether an event resolves must not depend on when the job ran.
        assert self.since("2026-07-30") % 86400 == 0


class TestThe2026Season:
    """
    The acceptance criterion, run end to end over recorded data.

    Events come from the recorded Liquipedia page; candidates from every
    OpenDota league that played a match in 2026, with its full match list; and
    the tracked teams are the 43 in the dataset as it stood at 1,605 matches —
    before the Esports World Cup and 1win Essence II were backfilled, which is
    the state the resolution was designed against and the only one in which
    those two events score anything but 100%.
    """

    # Class-scoped, because summarising 29,000 matches for each of seventeen
    # assertions is four seconds the suite does not have to spend.
    @pytest.fixture(scope="class")
    @staticmethod
    def events(liquipedia_tier1_html):
        return events_in_year(parse_tier1_events(liquipedia_tier1_html), 2026)

    @pytest.fixture(scope="class")
    @staticmethod
    def summaries(league_matches_2026):
        catalogue = league_catalogue([
            {"leagueid": league["leagueid"], "name": league["name"],
             "tier": league["tier"]}
            for league in league_matches_2026["leagues"]
        ])
        matches = [m for league in league_matches_2026["leagues"]
                   for m in league["matches"]]

        return summarise_leagues(matches, catalogue)

    @pytest.fixture(scope="class")
    @staticmethod
    def resolutions(events, summaries, league_matches_2026):
        return {
            resolution["event"]: resolution
            for resolution in resolve_tier1_events(
                events, summaries, set(league_matches_2026["known_team_ids"])
            )
        }

    @pytest.mark.parametrize("event_name, league_id", [
        ("DreamLeague Season 28",   19269),
        ("PGL Wallachia Season 7",  19435),
        ("ESL One Birmingham 2026", 19422),
        ("PGL Wallachia Season 8",  19543),
        ("DreamLeague Season 29",   19696),
        ("Esports World Cup 2026",  19785),
    ])
    def test_six_windows_hold_exactly_one_candidate(
        self, resolutions, event_name, league_id
    ):
        resolution = resolutions[event_name]

        assert resolution["league_id"] == league_id
        assert resolution["ambiguous"] is False

    @pytest.mark.parametrize("event_name, winner, winner_score, loser, loser_score", [
        # The one that matters most: 1win Essence II is the tournament that ran
        # for a week in the dashboard's blind spot, and it wins on 85.8% — which
        # is why the ranking is relative and no threshold would do.
        ("1win Essence II", 20009, 0.8583, 19917, 0.0903),
        ("BLAST SLAM VII",  19101, 1.0,    19699, 0.1908),
        ("BLAST SLAM VI",   19099, 1.0,    19290, 0.5393),
    ])
    def test_three_windows_hold_two_and_the_right_one_wins(
        self, resolutions, event_name, winner, winner_score, loser, loser_score
    ):
        resolution = resolutions[event_name]

        assert resolution["league_id"] == winner
        assert resolution["ambiguous"] is True
        assert [c["league_id"] for c in resolution["candidates"]] == [winner, loser]
        assert resolution["candidates"][0]["overlap"] == pytest.approx(
            winner_score, abs=5e-5
        )
        assert resolution["candidates"][1]["overlap"] == pytest.approx(
            loser_score, abs=5e-5
        )

    @pytest.mark.parametrize("event_name", [
        "The International 2026",
        "PGL Wallachia Season 9",
        "BLAST SLAM VIII",
        "BLAST SLAM IX",
    ])
    def test_the_four_unplayed_events_are_gaps(self, resolutions, event_name):
        resolution = resolutions[event_name]

        assert resolution["league_id"] is None
        assert resolution["candidates"] == []

    def test_every_event_is_accounted_for(self, resolutions):
        resolved = [r for r in resolutions.values() if r["league_id"] is not None]
        gaps     = [r for r in resolutions.values() if r["league_id"] is None]

        assert len(resolved) == 9
        assert len(gaps) == 4

    def test_no_qualifier_wins_a_window(self, resolutions):
        # Qualifiers need no rule of their own — no name rule and no tier rule.
        # That is what makes this survive where name matching does not: PGL
        # Wallachia Season #7's Asia Closed Qualifiers outscore the Esports
        # World Cup on team overlap, so nothing but the date could save it.
        winners = {r["league_id"] for r in resolutions.values()}

        assert winners.isdisjoint({19244, 19245, 19246, 19247, 19248,
                                   19089, 19090, 19448, 19520, 19699})

    def test_most_qualifiers_are_excluded_by_the_window_alone(self, resolutions):
        # Nine of the ten never become candidates at all: they run outside the
        # main event's window, so the date test drops them before anything is
        # scored.
        candidates = {c["league_id"]
                      for r in resolutions.values() for c in r["candidates"]}

        assert candidates.isdisjoint({19244, 19245, 19246, 19247, 19248,
                                      19089, 19090, 19448, 19520})

    def test_but_one_qualifier_does_fall_inside_a_window_and_loses_on_teams(
        self, resolutions
    ):
        # ADR-0001 says qualifiers "fall outside main event windows", and for
        # nine of ten they do. Road To EWC 2026 Regional Qualifiers ran 29 May
        # to 5 June, inside BLAST SLAM VII's 26 May to 7 June, and it is the
        # overlap ranking that beats it — not the date. Recorded here because
        # the claim is load-bearing and is not quite true.
        candidates = resolutions["BLAST SLAM VII"]["candidates"]

        assert [c["league_id"] for c in candidates] == [19101, 19699]
        assert candidates[1]["overlap"] < candidates[0]["overlap"]

    def test_the_two_sources_disagree_on_the_names_that_matter(
        self, resolutions, summaries
    ):
        # Not decoration: it is the reason names are never compared. Two of the
        # nine mappings pair an event and a league that no string comparison
        # would ever put together, and both resolve on dates without noticing.
        differing = {
            name: summaries[resolution["league_id"]]["name"]
            for name, resolution in resolutions.items()
            if resolution["league_id"] is not None
            and summaries[resolution["league_id"]]["name"] != name
        }

        assert differing == {
            "PGL Wallachia Season 7": "PGL Wallachia 2026 Season 7",
            "PGL Wallachia Season 8": "PGL Wallachia 2026 Season 8",
        }
