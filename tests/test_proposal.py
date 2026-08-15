"""
The approval mechanism, as data: what a pull request proposes, what it says, and
what merging or closing it does to the ledger.

Nothing here opens a pull request. These are the pure halves of
`tier1-pipeline-automation/15` — the queue, the body, the marker that carries
the answer back when the pull request is closed, and the two ledger edits the
two outcomes make. `propose_coverage.py` is the shell that runs git and `gh`,
and `tests/test_proposal_shell.py` holds that without executing either.

The queue is deliberately `core.awaiting_verdict`'s, not a second one. The
dashboard's Upcoming tab and the run's ATTENTION lines already share it, and a
pull request proposing a league the tab does not list would be a third opinion.
"""
import pytest

from core import (
    LEDGER_ACTIVE,
    LEDGER_PENDING,
    LEDGER_REJECTED,
    proposal_body,
    proposal_marker,
    proposal_title,
    proposals,
    proposed_league_ids,
    record_pull_request,
    record_verdicts,
    tier1_resolution_state,
)


def candidate(league_id, name, overlap=1.0, coverage=1.0, match_count=60):
    return {
        "league_id"  : league_id,
        "name"       : name,
        "overlap"    : overlap,
        "coverage"   : coverage,
        "match_count": match_count,
    }


def record(name, start, end, league_id, league_name, verdict=LEDGER_PENDING,
           candidates=None, **extras):
    """One `data/tier1_resolution.json` event record."""
    listed = candidates or ([candidate(league_id, league_name)]
                            if league_id is not None else [])
    return {
        "event"      : name,
        "start_date" : start,
        "end_date"   : end,
        "league_id"  : league_id,
        "league_name": league_name,
        "verdict"    : verdict,
        "ambiguous"  : len(listed) > 1,
        "candidates" : listed,
        **extras,
    }


TI = record("The International 2026", "2026-08-13", "2026-08-23",
            19719, "The International 2026")

CONTESTED = record(
    "1win Essence II", "2026-07-30", "2026-08-05", 20009, "1win Essence II",
    candidates=[
        candidate(20009, "1win Essence II", 1.0, 1.0, 60),
        candidate(19917, "The Games of the Future 2026", 0.0903, 0.8571, 72),
    ],
)


# ── What gets proposed ────────────────────────────────────────────────────────


class TestTheQueue:
    def test_a_league_nobody_has_judged_is_proposed(self):
        assert [entry["league_id"] for entry in proposals([TI])] == [19719]

    def test_a_settled_league_is_not(self):
        # Both directions of settled: covering it and refusing it are equally
        # decisions on record, and a decision on record is never proposed again.
        active   = dict(TI, verdict=LEDGER_ACTIVE)
        rejected = dict(TI, verdict=LEDGER_REJECTED)

        assert proposals([active, rejected]) == []

    def test_a_gap_is_not_proposed(self):
        # There is no ledger line to change: OpenDota has no league for it.
        assert proposals([record("BLAST SLAM VIII", "2026-09-29", "2026-10-11",
                                 None, None)]) == []

    def test_nothing_awaiting_is_no_proposals(self):
        assert proposals([]) == []
        assert proposals(None) == []

    def test_the_evidence_travels_with_the_proposal(self):
        # Everything the acceptance criteria say the body must carry has to be
        # on the record before any of it can be rendered.
        proposal = proposals([CONTESTED])[0]

        assert proposal["event"]       == "1win Essence II"
        assert proposal["start_date"]  == "2026-07-30"
        assert proposal["end_date"]    == "2026-08-05"
        assert proposal["league_id"]   == 20009
        assert proposal["league_name"] == "1win Essence II"
        assert proposal["match_count"] == 60
        assert proposal["overlap"]     == 1.0
        assert proposal["ambiguous"]   is True

    def test_the_losing_candidates_are_carried_as_also_seen(self):
        # The safety net: a genuine event whose field this project does not
        # track yet ranks low, and a list of five lines is the difference
        # between a filter that is trusted and one that is wondered about.
        proposal = proposals([CONTESTED])[0]

        assert [seen["league_id"] for seen in proposal["also_seen"]] == [19917]

    def test_the_winner_is_never_in_also_seen(self):
        assert proposals([TI])[0]["also_seen"] == []


# ── The pull request itself ───────────────────────────────────────────────────


class TestTheTitle:
    def test_one_proposal_names_the_event_and_the_league(self):
        assert proposal_title(proposals([TI])) == \
            "Cover The International 2026 (league 19719)"

    def test_several_are_counted(self):
        title = proposal_title(proposals([TI, CONTESTED]))

        assert title == "Cover 2 newly detected Tier 1 leagues"


class TestTheBody:
    def test_it_states_what_merging_and_closing_do(self):
        # The whole mechanism, written where the decision is taken. Wade reads
        # this on a phone, and "which button means no" cannot be folklore.
        body = proposal_body(proposals([TI]))

        assert "Merging" in body and "Closing" in body

    def test_the_evidence_is_all_there(self):
        body = proposal_body(proposals([CONTESTED]))

        assert "1win Essence II"  in body   # the event, and the league
        assert "2026-07-30"       in body   # its window
        assert "2026-08-05"       in body
        assert "20009"            in body   # the OpenDota league id
        assert "60"               in body   # matches played
        assert "100.0%"           in body   # team overlap

    def test_a_contested_window_says_so(self):
        assert "contested" in proposal_body(proposals([CONTESTED]))

    def test_an_uncontested_one_does_not_claim_to_have_judged(self):
        assert "contested" not in proposal_body(proposals([TI]))

    def test_the_losing_candidates_are_listed_and_named(self):
        body = proposal_body(proposals([CONTESTED]))

        assert "not proposed" in body
        assert "The Games of the Future 2026" in body
        assert "19917" in body

    def test_events_under_way_with_no_league_are_reported_too(self):
        # A gap cannot be proposed — there is no ledger line to change — but an
        # overdue one is the loudest news the resolver has, and a pull request
        # already in front of Wade is where it is read.
        body = proposal_body(proposals([TI]), gaps=[{
            "event": "BLAST SLAM VIII", "start_date": "2026-08-01",
            "end_date": "2026-08-11", "state": "overdue",
        }])

        assert "BLAST SLAM VIII" in body

    def test_no_gaps_means_no_gap_section(self):
        assert "no league" not in proposal_body(proposals([TI])).lower()

    def test_liquipedia_is_credited(self):
        # The body renders event names and windows, which are calendar data.
        # Attribution is a condition of the free API, not a courtesy — ADR-0006.
        from core import LIQUIPEDIA_ATTRIBUTION

        assert LIQUIPEDIA_ATTRIBUTION in proposal_body(proposals([TI]))

    def test_the_body_carries_the_marker(self):
        assert proposed_league_ids(proposal_body(proposals([TI, CONTESTED]))) \
            == [19719, 20009]


class TestTheMarker:
    """
    How a closed pull request says which leagues it proposed. The body is the
    only thing GitHub hands the closing workflow that this project wrote itself
    — the branch may be gone, and the diff is a megabyte.
    """

    def test_it_round_trips(self):
        assert proposed_league_ids(proposal_marker([19719, 20009])) == \
            [19719, 20009]

    def test_it_is_an_html_comment_so_the_body_still_reads(self):
        assert proposal_marker([19719]).startswith("<!--")

    def test_ids_come_back_sorted_and_unique(self):
        assert proposed_league_ids(proposal_marker([20009, 19719, 19719])) == \
            [19719, 20009]

    def test_a_body_with_no_marker_proposes_nothing(self):
        # Somebody rewrote the body by hand. Nothing is rejected on a guess:
        # the league stays pending and is proposed again on the next run.
        assert proposed_league_ids("Looks good to me") == []
        assert proposed_league_ids("") == []
        assert proposed_league_ids(None) == []

    def test_prose_around_the_marker_does_not_reach_it(self):
        body = f"### Coverage\n\n{proposal_marker([19719])}\n\n1234 matches\n"

        assert proposed_league_ids(body) == [19719]


# ── What the two outcomes do ──────────────────────────────────────────────────


LEDGER = {"leagues": [
    {"league_id": 19719, "name": "The International 2026",
     "tier1_event": "The International 2026", "verdict": LEDGER_PENDING},
    {"league_id": 20009, "name": "1win Essence II",
     "tier1_event": "1win Essence II", "verdict": LEDGER_PENDING},
    {"league_id": 17119, "name": "DreamLeague Season 29",
     "tier1_event": None, "verdict": LEDGER_ACTIVE},
]}


class TestRecordingAVerdict:
    def test_merging_covers_the_league(self):
        ledger, changes = record_verdicts(LEDGER, [19719], LEDGER_ACTIVE)

        assert changes == [(19719, LEDGER_PENDING, LEDGER_ACTIVE)]
        assert ledger["leagues"][0]["verdict"] == LEDGER_ACTIVE

    def test_closing_records_the_refusal(self):
        # So it is never proposed again — that is what a verdict is for.
        ledger, _ = record_verdicts(LEDGER, [19719], LEDGER_REJECTED)

        assert ledger["leagues"][0]["verdict"] == LEDGER_REJECTED

    def test_a_league_nobody_named_is_untouched(self):
        ledger, changes = record_verdicts(LEDGER, [19719], LEDGER_ACTIVE)

        assert [entry["verdict"] for entry in ledger["leagues"][1:]] == \
            [LEDGER_PENDING, LEDGER_ACTIVE]
        assert len(changes) == 1

    def test_a_decision_already_taken_is_never_overwritten(self):
        # The case: a league proposed on Monday and covered by hand on Tuesday,
        # whose pull request is closed on Wednesday. Closing it must not
        # un-cover a tournament a human deliberately took on.
        ledger, changes = record_verdicts(LEDGER, [17119], LEDGER_REJECTED)

        assert changes == []
        assert ledger["leagues"][2]["verdict"] == LEDGER_ACTIVE

    def test_a_league_the_ledger_has_never_seen_is_not_invented(self):
        # Coverage is `data/leagues.json`, and an entry appearing from a pull
        # request body rather than from OpenDota's league list would be a
        # league nobody can trace.
        ledger, changes = record_verdicts(LEDGER, [999999], LEDGER_ACTIVE)

        assert changes == []
        assert len(ledger["leagues"]) == 3

    def test_the_original_ledger_is_not_mutated(self):
        record_verdicts(LEDGER, [19719], LEDGER_ACTIVE)

        assert LEDGER["leagues"][0]["verdict"] == LEDGER_PENDING

    def test_nothing_named_changes_nothing(self):
        ledger, changes = record_verdicts(LEDGER, [], LEDGER_ACTIVE)

        assert changes == []
        assert ledger["leagues"] == LEDGER["leagues"]

    def test_the_rest_of_the_document_survives(self):
        # `_comment`, `source` and `seeded_on` are the file's own explanation
        # of itself, and rewriting it without them is a worse file.
        ledger, _ = record_verdicts(dict(LEDGER, source="opendota"), [19719],
                                    LEDGER_ACTIVE)

        assert ledger["source"] == "opendota"


class TestRecordingThePullRequest:
    URL = "https://github.com/Wadith-ops/dota2-pro-match-dashboard/pull/7"

    def test_the_url_lands_on_the_proposed_event(self):
        records, changed = record_pull_request([TI], self.URL, [19719])

        assert changed is True
        assert records[0]["pull_request"] == self.URL

    def test_an_event_the_pull_request_does_not_propose_is_untouched(self):
        records, _ = record_pull_request([TI, CONTESTED], self.URL, [19719])

        assert records[1].get("pull_request") is None

    def test_recording_the_same_url_twice_is_not_a_change(self):
        # The file is committed and pushed. A run that re-derived the same
        # answer must not put a commit in front of Wade saying nothing.
        once, _    = record_pull_request([TI], self.URL, [19719])
        _, changed = record_pull_request(once, self.URL, [19719])

        assert changed is False

    def test_the_records_are_not_mutated(self):
        record_pull_request([TI], self.URL, [19719])

        assert TI.get("pull_request") is None


class TestTheUrlSurvivesTheNextRun:
    """
    The resolver rewrites `data/tier1_resolution.json` from the ledger and the
    calendar, and it examines an event exactly once. Without carrying the link
    forward, the run after a pull request opened would blank it — and the
    Upcoming tab's Review link would fall back to "every open PR" while the very
    pull request it names sat waiting.
    """

    EVENTS = [{"name": "The International 2026",
               "start_date": "2026-08-13", "end_date": "2026-08-23"}]
    LEDGER = {"leagues": [{"league_id": 19719, "name": "The International 2026",
                           "tier1_event": "The International 2026",
                           "verdict": LEDGER_PENDING}]}

    def test_a_recorded_url_is_carried_forward(self):
        previous = [dict(TI, pull_request="https://example.test/pull/7")]

        records = tier1_resolution_state(self.EVENTS, self.LEDGER,
                                         resolutions=None, previous=previous)

        assert records[0]["pull_request"] == "https://example.test/pull/7"

    def test_an_event_with_no_pull_request_carries_none(self):
        records = tier1_resolution_state(self.EVENTS, self.LEDGER)

        assert records[0]["pull_request"] is None
