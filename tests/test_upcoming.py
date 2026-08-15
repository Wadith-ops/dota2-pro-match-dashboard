"""
What the Upcoming tab reads: the two questions `data/tier1_resolution.json`
answers once it exists.

- **Where is coverage missing?** `coverage_gaps` — Tier 1 events with no
  OpenDota league, and whether each is merely early or actually late.
- **What is waiting on a decision?** `awaiting_verdict` — events that resolved
  to a league nobody has judged.

Both are pure reads of the report the resolver writes. The tab renders them; it
never computes them, and it never writes anything back.
"""
from datetime import date

import pytest

from core import (
    GAP_OVERDUE,
    GAP_UPCOMING,
    LEDGER_ACTIVE,
    LEDGER_PENDING,
    LEDGER_REJECTED,
    awaiting_verdict,
    coverage_gaps,
)


def gap(name, start, end):
    """A resolution record for an event with no league — a known gap."""
    return {
        "event": name, "start_date": start, "end_date": end,
        "league_id": None, "league_name": None, "verdict": None,
        "ambiguous": False, "candidates": [],
    }


def resolved(name, start, end, league_id, league_name, verdict,
             match_count=100, overlap=1.0, ambiguous=False, extras=None):
    """A resolution record for an event that found its league."""
    candidates = [{
        "league_id": league_id, "name": league_name,
        "overlap": overlap, "coverage": 1.0, "match_count": match_count,
    }]
    return {
        "event": name, "start_date": start, "end_date": end,
        "league_id": league_id, "league_name": league_name,
        "verdict": verdict, "ambiguous": ambiguous,
        "candidates": candidates + list(extras or []),
    }


class TestCoverageGaps:
    def test_only_events_with_no_league_are_gaps(self):
        gaps = coverage_gaps([
            gap("The International 2026", "2026-08-13", "2026-08-23"),
            resolved("Esports World Cup 2026", "2026-07-07", "2026-07-19",
                     19785, "Esports World Cup 2026", LEDGER_ACTIVE),
        ], date(2026, 8, 11))

        assert [record["event"] for record in gaps] == ["The International 2026"]

    def test_an_event_yet_to_start_is_upcoming(self):
        gaps = coverage_gaps(
            [gap("The International 2026", "2026-08-13", "2026-08-23")],
            date(2026, 8, 11),
        )

        assert gaps[0]["state"] == GAP_UPCOMING

    def test_a_gap_past_its_start_date_is_overdue(self):
        # The case that means something is broken rather than merely early: the
        # tournament is being played and this project has no data for it.
        gaps = coverage_gaps(
            [gap("The International 2026", "2026-08-13", "2026-08-23")],
            date(2026, 8, 14),
        )

        assert gaps[0]["state"] == GAP_OVERDUE

    def test_the_first_day_of_an_event_is_already_overdue(self):
        # Matches are played on day one. A gap on day one is a gap.
        gaps = coverage_gaps(
            [gap("The International 2026", "2026-08-13", "2026-08-23")],
            date(2026, 8, 13),
        )

        assert gaps[0]["state"] == GAP_OVERDUE

    def test_overdue_gaps_come_first_then_the_soonest_upcoming(self):
        gaps = coverage_gaps([
            gap("PGL Wallachia Season 12", "2027-10-19", "2027-10-31"),
            gap("BLAST SLAM VIII", "2026-09-29", "2026-10-11"),
            gap("The International 2026", "2026-08-13", "2026-08-23"),
        ], date(2026, 8, 14))

        assert [record["event"] for record in gaps] == [
            "The International 2026",   # overdue
            "BLAST SLAM VIII",          # then soonest first
            "PGL Wallachia Season 12",
        ]

    def test_an_event_with_no_start_date_is_upcoming_not_overdue(self):
        # Liquipedia writes "TBD" for an unscheduled event, and the parser keeps
        # it as no date. Nothing is late about a tournament with no date.
        gaps = coverage_gaps(
            [gap("Some Unscheduled Major", None, None)],
            date(2026, 8, 14),
        )

        assert gaps[0]["state"] == GAP_UPCOMING

    def test_no_records_is_no_gaps(self):
        assert coverage_gaps([], date(2026, 8, 11)) == []
        assert coverage_gaps(None, date(2026, 8, 11)) == []


class TestAwaitingVerdict:
    def test_a_pending_league_is_awaiting(self):
        awaiting = awaiting_verdict([
            resolved("Esports World Cup 2026", "2026-07-07", "2026-07-19",
                     19785, "Esports World Cup 2026", LEDGER_PENDING),
        ])

        assert [record["league_id"] for record in awaiting] == [19785]

    @pytest.mark.parametrize("verdict", [LEDGER_ACTIVE, LEDGER_REJECTED])
    def test_a_settled_verdict_is_not_awaiting(self, verdict):
        # `rejected` is as settled as `active`. A decision on record is never
        # proposed again — the same rule `resolution_problems` follows.
        awaiting = awaiting_verdict([
            resolved("Esports World Cup 2026", "2026-07-07", "2026-07-19",
                     19785, "Esports World Cup 2026", verdict),
        ])

        assert awaiting == []

    def test_a_league_with_no_recorded_verdict_is_awaiting(self):
        awaiting = awaiting_verdict([
            resolved("Esports World Cup 2026", "2026-07-07", "2026-07-19",
                     19785, "Esports World Cup 2026", None),
        ])

        assert len(awaiting) == 1

    def test_a_gap_is_never_awaiting_a_verdict(self):
        # There is nothing to approve. It is a gap, and it belongs in the other
        # list.
        assert awaiting_verdict(
            [gap("The International 2026", "2026-08-13", "2026-08-23")]
        ) == []

    def test_it_carries_the_winning_candidates_evidence(self):
        awaiting = awaiting_verdict([
            resolved("1win Essence II", "2026-07-30", "2026-08-05",
                     20009, "1win Essence II", LEDGER_PENDING,
                     match_count=60, overlap=0.858, ambiguous=True,
                     extras=[{
                         "league_id": 19917, "name": "The Games of the Future 2026",
                         "overlap": 0.0903, "coverage": 0.8571, "match_count": 72,
                     }]),
        ])

        assert awaiting[0]["match_count"] == 60
        assert awaiting[0]["overlap"] == pytest.approx(0.858)
        assert awaiting[0]["ambiguous"] is True
        assert awaiting[0]["runners_up"] == 1

    def test_evidence_is_absent_rather_than_wrong_when_no_candidate_matches(self):
        # Reachable when the ranking was never carried forward. The league is
        # still awaiting a verdict; only the numbers behind it are missing.
        record = resolved("Esports World Cup 2026", "2026-07-07", "2026-07-19",
                          19785, "Esports World Cup 2026", LEDGER_PENDING)
        record["candidates"] = []

        awaiting = awaiting_verdict([record])

        assert awaiting[0]["match_count"] is None
        assert awaiting[0]["overlap"] is None
        assert awaiting[0]["runners_up"] == 0

    def test_the_evidence_is_the_winners_own_row(self):
        # `candidates` is ranked, not keyed. Reading the first entry would
        # attribute a runner-up's match count to the league that won.
        record = resolved("1win Essence II", "2026-07-30", "2026-08-05",
                          20009, "1win Essence II", LEDGER_PENDING,
                          match_count=60, overlap=1.0)
        record["candidates"].reverse()
        record["candidates"].insert(0, {
            "league_id": 19917, "name": "The Games of the Future 2026",
            "overlap": 0.0903, "coverage": 0.8571, "match_count": 72,
        })

        assert awaiting_verdict([record])[0]["match_count"] == 60

    def test_the_newest_event_comes_first(self):
        awaiting = awaiting_verdict([
            resolved("Esports World Cup 2026", "2026-07-07", "2026-07-19",
                     19785, "Esports World Cup 2026", LEDGER_PENDING),
            resolved("1win Essence II", "2026-07-30", "2026-08-05",
                     20009, "1win Essence II", LEDGER_PENDING),
        ])

        assert [record["event"] for record in awaiting] == [
            "1win Essence II", "Esports World Cup 2026",
        ]

    def test_it_carries_a_pull_request_link_when_one_is_recorded(self):
        # `propose_coverage.py` writes this field when it opens the pull
        # request (`15`); reading it here is what makes the tab link to the
        # proposal itself rather than to every open pull request.
        record = resolved("Esports World Cup 2026", "2026-07-07", "2026-07-19",
                          19785, "Esports World Cup 2026", LEDGER_PENDING)
        record["pull_request"] = "https://github.com/x/y/pull/42"

        assert awaiting_verdict([record])[0]["pull_request"] == (
            "https://github.com/x/y/pull/42"
        )

    def test_no_records_is_nothing_awaiting(self):
        assert awaiting_verdict([]) == []
        assert awaiting_verdict(None) == []


class TestTheCommittedReport:
    """
    The same two reads against `data/tier1_resolution.json` as it stands. This
    is what the deployed tab will actually render.
    """

    def test_every_gap_in_the_report_is_reported_as_one(self, tier1_resolution):
        gaps = coverage_gaps(tier1_resolution["events"], date(2026, 8, 11))

        assert len(gaps) == tier1_resolution["gap_count"]

    def test_the_international_is_a_known_gap_before_it_starts(self, tier1_resolution):
        gaps = coverage_gaps(tier1_resolution["events"], date(2026, 8, 11))
        international = next(
            record for record in gaps
            if record["event"] == "The International 2026"
        )

        assert international["state"] == GAP_UPCOMING
        assert international["start_date"] == "2026-08-13"

    def test_it_turns_overdue_once_the_tournament_is_under_way(self, tier1_resolution):
        gaps = coverage_gaps(tier1_resolution["events"], date(2026, 8, 20))
        international = next(
            record for record in gaps
            if record["event"] == "The International 2026"
        )

        assert international["state"] == GAP_OVERDUE

    def test_nothing_is_awaiting_a_verdict_today(self, tier1_resolution):
        # Every league the resolver has mapped is already `active`. This is the
        # quiet state, and the tab has to render it without a table.
        assert awaiting_verdict(tier1_resolution["events"]) == []
