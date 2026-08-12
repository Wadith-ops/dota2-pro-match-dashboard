"""
Auditing the back catalogue against Liquipedia's Tier 1 list.

The daily resolver only looks at events it has never mapped, so every answer
already on record goes untested for ever — including the five 2025 leagues that
were chosen by hand before any of this existed. An audit re-derives a bounded
period in full and reports what it finds, in both directions: a Tier 1 event
with no league, and a league being fetched that no Tier 1 event claims.

Everything here is pure. Resolutions and a ledger in, findings out; findings in,
a report out. Walking OpenDota for the candidates is `audit_coverage.py`'s job.

See `tier1-pipeline-automation/14`.
"""
from datetime import datetime

import pytest

from core import (
    AUDIT_COVERED,
    AUDIT_DECLINED,
    AUDIT_GAP,
    AUDIT_LISTED,
    AUDIT_UNLISTED,
    AUDIT_UNTRACKED,
    LEDGER_ACTIVE,
    LEDGER_PENDING,
    LEDGER_REJECTED,
    audit_findings,
    audit_totals,
    audit_tracked_leagues,
    dataset_start_date,
    events_in_range,
    format_audit_next_steps,
    format_audit_report,
    league_catalogue,
    resolve_tier1_events,
    summarise_leagues,
)


def candidate(league_id, name="A League", overlap=1.0, coverage=1.0, matches=40):
    return {"league_id": league_id, "name": name, "overlap": overlap,
            "coverage": coverage, "match_count": matches}


def resolution(event, league_id, candidates=None, start="2025-10-14",
               end="2025-11-09", name="A League"):
    """One entry as `resolve_tier1_events` returns it."""
    candidates = (candidates if candidates is not None
                  else ([candidate(league_id, name)] if league_id else []))
    return {
        "event"      : event,
        "start_date" : start,
        "end_date"   : end,
        "league_id"  : league_id,
        "league_name": name if league_id else None,
        "overlap"    : candidates[0]["overlap"] if candidates else None,
        "ambiguous"  : len(candidates) > 1,
        "candidates" : candidates,
    }


def ledger(*entries):
    return {"leagues": [
        {"league_id": league_id, "name": name, "tier1_event": event,
         "verdict": verdict}
        for league_id, name, event, verdict in entries
    ]}


class TestWhereTheAuditStarts:
    """
    The starting bound is derived from the data, not written down. An event
    that finished before the dataset began is outside its intended range rather
    than a coverage gap, and where that line falls moves every time the back
    catalogue does.
    """

    @staticmethod
    def row(day):
        """One flat row, played at midday UTC so no assertion turns on the
        side of midnight a timezone conversion lands on."""
        return {"start_time": int(
            datetime.fromisoformat(f"{day}T12:00:00+00:00").timestamp()
        )}

    def test_it_is_the_day_of_the_earliest_match(self):
        rows = [self.row("2025-12-10"), self.row("2025-10-14"),
                self.row("2026-08-11")]

        assert dataset_start_date(rows) == "2025-10-14"

    def test_a_row_with_no_start_time_does_not_decide_it(self):
        # A None reaching the min would sort below every timestamp and answer
        # the epoch, which reads as a dataset going back to 1970.
        rows = [{"start_time": None}, self.row("2025-10-14")]

        assert dataset_start_date(rows) == "2025-10-14"

    def test_no_matches_at_all_answers_nothing(self):
        assert dataset_start_date([]) is None


class TestSelectingThePeriod:
    EVENTS = [
        {"name": "The International 2025",  "start_date": "2025-09-04"},
        {"name": "BLAST Slam IV",           "start_date": "2025-10-14"},
        {"name": "DreamLeague Season 27",   "start_date": "2025-12-10"},
        {"name": "DreamLeague Season 28",   "start_date": "2026-01-19"},
        {"name": "BLAST SLAM IX",           "start_date": None},
    ]

    def test_events_inside_the_range_come_back_oldest_first(self):
        chosen = events_in_range(self.EVENTS, "2025-10-01", "2026-01-31")

        assert [event["name"] for event in chosen] == [
            "BLAST Slam IV", "DreamLeague Season 27", "DreamLeague Season 28",
        ]

    def test_both_ends_are_inclusive(self):
        # The dataset's first match was played on its first day, so an
        # exclusive bound would drop the event the audit starts from.
        chosen = events_in_range(self.EVENTS, "2025-10-14", "2025-12-10")

        assert [event["name"] for event in chosen] == [
            "BLAST Slam IV", "DreamLeague Season 27",
        ]

    def test_an_event_before_the_range_is_outside_the_dataset_not_a_gap(self):
        chosen = events_in_range(self.EVENTS, "2025-10-01", "2026-12-31")

        assert "The International 2025" not in [event["name"] for event in chosen]

    def test_an_event_with_no_published_date_is_not_in_any_range(self):
        chosen = events_in_range(self.EVENTS, "2000-01-01", "2030-12-31")

        assert "BLAST SLAM IX" not in [event["name"] for event in chosen]


class TestWhatTheAuditFinds:
    def test_an_event_whose_league_is_being_fetched_is_covered(self):
        findings = audit_findings(
            [resolution("BLAST Slam IV", 17419)],
            ledger((17419, "Slam IV", "BLAST Slam IV", LEDGER_ACTIVE)),
        )

        assert findings[0]["finding"] == AUDIT_COVERED
        assert findings[0]["mismatch"] is False

    def test_an_event_resolving_to_a_league_nobody_judged_is_the_queue(self):
        findings = audit_findings(
            [resolution("FISSURE PLAYGROUND 2", 18863)],
            ledger((18863, "FISSURE PLAYGROUND 2", None, LEDGER_PENDING)),
        )

        assert findings[0]["finding"] == AUDIT_UNTRACKED
        assert findings[0]["verdict"] == LEDGER_PENDING

    def test_a_league_the_ledger_has_never_heard_of_is_also_the_queue(self):
        # An unrecorded league is not a decided one. It fails the way a
        # rejection does, so it goes on the side of the line that gets read.
        findings = audit_findings([resolution("BLAST Slam V", 17420)], ledger())

        assert findings[0]["finding"] == AUDIT_UNTRACKED
        assert findings[0]["verdict"] is None

    def test_a_league_that_was_turned_down_is_reported_as_declined(self):
        # Not a fault — that is what a verdict is for. But an audit is the one
        # moment a decision on record is worth reading again.
        findings = audit_findings(
            [resolution("The International 2025", 18324)],
            ledger((18324, "The International 2025", "The International 2025",
                    LEDGER_REJECTED)),
        )

        assert findings[0]["finding"] == AUDIT_DECLINED

    def test_an_event_with_no_candidate_at_all_is_a_gap(self):
        findings = audit_findings([resolution("PGL Wallachia Season 9", None)],
                                  ledger())

        assert findings[0]["finding"] == AUDIT_GAP
        assert findings[0]["league_id"] is None
        assert findings[0]["match_count"] is None

    def test_a_contested_window_carries_its_ranking(self):
        findings = audit_findings(
            [resolution("BLAST Slam IV", 17419,
                        [candidate(17419, "Slam IV"),
                         candidate(18863, "FISSURE PLAYGROUND 2", matches=52)])],
            ledger((17419, "Slam IV", "BLAST Slam IV", LEDGER_ACTIVE)),
        )

        assert findings[0]["ambiguous"] is True
        assert [c["league_id"] for c in findings[0]["candidates"]] == [17419, 18863]

    def test_the_figures_come_from_the_winners_own_row_not_the_first(self):
        # `candidates` is ranked, not keyed. Reading `candidates[0]` would
        # report the wrong league's match count beside the right league's id.
        findings = audit_findings(
            [dict(resolution("BLAST Slam IV", 18863,
                             [candidate(17419, "Slam IV", matches=96),
                              candidate(18863, "FISSURE PLAYGROUND 2",
                                        matches=52)]),
                  league_id=18863)],
            ledger((18863, "FISSURE PLAYGROUND 2", None, LEDGER_PENDING)),
        )

        assert findings[0]["match_count"] == 52


class TestDisagreeingWithTheLedger:
    """
    The resolver writes its answer onto the ledger and never clears one, so a
    mapping made under an older ranking survives untested for ever. Re-deriving
    it is most of what an audit is for.
    """

    def test_a_mapping_this_run_reproduces_is_not_a_mismatch(self):
        findings = audit_findings(
            [resolution("BLAST Slam IV", 17419)],
            ledger((17419, "Slam IV", "BLAST Slam IV", LEDGER_ACTIVE)),
        )

        assert findings[0]["mismatch"] is False

    def test_a_different_league_this_run_is_a_mismatch(self):
        findings = audit_findings(
            [resolution("BLAST Slam IV", 18863)],
            ledger((17419, "Slam IV", "BLAST Slam IV", LEDGER_ACTIVE),
                   (18863, "FISSURE PLAYGROUND 2", None, LEDGER_PENDING)),
        )

        assert findings[0]["mismatch"] is True
        assert findings[0]["ledger_league_id"] == 17419

    def test_a_mapped_event_this_run_cannot_resolve_at_all_is_a_mismatch(self):
        # The ledger claims an answer this run cannot stand behind. Worth a
        # human reading before anything is changed — which is why the audit
        # reports it rather than clearing the mapping.
        findings = audit_findings(
            [resolution("BLAST Slam IV", None)],
            ledger((17419, "Slam IV", "BLAST Slam IV", LEDGER_ACTIVE)),
        )

        assert findings[0]["mismatch"] is True

    def test_an_event_the_ledger_never_mapped_is_not_a_mismatch(self):
        findings = audit_findings([resolution("BLAST Slam V", 17420)], ledger())

        assert findings[0]["mismatch"] is False
        assert findings[0]["ledger_league_id"] is None


class TestTheCheckRunTheOtherWay:
    EVENTS = [{"name": "BLAST Slam IV"}, {"name": "The International 2026"}]

    def test_a_tracked_league_a_tier1_event_claims_is_listed(self):
        tracked = audit_tracked_leagues(
            ledger((17419, "Slam IV", "BLAST Slam IV", LEDGER_ACTIVE)),
            self.EVENTS,
        )

        assert tracked[0]["finding"] == AUDIT_LISTED
        assert tracked[0]["reason"] == ""

    def test_a_tracked_league_nothing_resolved_to_is_unlisted(self):
        tracked = audit_tracked_leagues(
            ledger((19719, "The International 2026", None, LEDGER_ACTIVE)),
            self.EVENTS,
        )

        assert tracked[0]["finding"] == AUDIT_UNLISTED
        assert tracked[0]["reason"] == "no Tier 1 event resolved to it"

    def test_a_tracked_league_claiming_an_event_off_the_list_says_which(self):
        # Different news from the above: the mapping is stale rather than
        # absent, so the report has to name the event it no longer finds.
        tracked = audit_tracked_leagues(
            ledger((18434, "Кубок России", "FISSURE Universe: Episode 6",
                    LEDGER_ACTIVE)),
            self.EVENTS,
        )

        assert tracked[0]["finding"] == AUDIT_UNLISTED
        assert "FISSURE Universe: Episode 6" in tracked[0]["reason"]

    def test_a_league_that_is_not_being_fetched_is_not_examined(self):
        # A rejected league no event claims is not a finding — it is ten
        # thousand of them.
        tracked = audit_tracked_leagues(
            ledger((18324, "The International 2025", None, LEDGER_REJECTED),
                   (99999, "Some Showmatch", None, LEDGER_PENDING)),
            self.EVENTS,
        )

        assert tracked == []


class TestTheTotals:
    FINDINGS = [
        resolution("A", 1), resolution("B", None),
        resolution("C", 3, [candidate(3), candidate(4)]),
    ]

    @pytest.fixture
    def totals(self):
        findings = audit_findings(
            self.FINDINGS,
            ledger((1, "One", "A", LEDGER_ACTIVE), (3, "Three", None,
                                                    LEDGER_PENDING)),
        )
        tracked = audit_tracked_leagues(
            ledger((1, "One", "A", LEDGER_ACTIVE),
                   (9, "Nine", None, LEDGER_ACTIVE)),
            [{"name": "A"}],
        )
        return audit_totals(findings, tracked)

    def test_every_event_is_counted_under_exactly_one_finding(self, totals):
        assert totals["events"] == 3
        assert (totals[AUDIT_COVERED] + totals[AUDIT_UNTRACKED]
                + totals[AUDIT_DECLINED] + totals[AUDIT_GAP]) == 3

    def test_ambiguity_is_counted_alongside_the_finding_not_instead_of_it(
        self, totals
    ):
        # A contested window still resolved to something. Counting it as its own
        # class would make the four findings stop adding up to the events.
        assert totals["ambiguous"] == 1
        assert totals[AUDIT_UNTRACKED] == 1

    def test_the_reverse_check_is_counted_too(self, totals):
        assert totals["tracked"] == 2
        assert totals["unlisted"] == 1


class TestTheReport:
    FINDINGS = [
        resolution("BLAST Slam IV", 17419, start="2025-10-14", end="2025-11-09"),
        resolution("PGL Wallachia Season 9", None, start="2026-09-14",
                   end="2026-09-26"),
    ]

    @pytest.fixture
    def report(self):
        findings = audit_findings(
            self.FINDINGS, ledger((17419, "Slam IV", "BLAST Slam IV",
                                   LEDGER_ACTIVE)),
        )
        tracked = audit_tracked_leagues(
            ledger((17419, "Slam IV", "BLAST Slam IV", LEDGER_ACTIVE)),
            [{"name": "BLAST Slam IV"}],
        )
        return format_audit_report(findings, tracked, "2025-10-01", "2026-08-12")

    def test_it_states_the_period_it_covered(self, report):
        assert "2025-10-01 to 2026-08-12" in report

    def test_the_headline_counts_every_finding(self, report):
        assert "2 events audited: 1 covered" in report
        assert "1 with no league at all" in report

    def test_a_gap_is_named_with_its_window(self, report):
        assert "PGL Wallachia Season 9" in report
        assert "2026-09-14 – 2026-09-26" in report

    def test_an_empty_section_says_so_rather_than_disappearing(self, report):
        # "We looked and found none" is the answer an audit exists to give. A
        # section that vanishes when empty cannot be told from one nobody ran.
        assert "(none)" in report

    def test_no_events_is_reported_as_nothing_checked_not_as_nothing_missing(self):
        # The one thing this report must never do is read as a clean bill of
        # health when the calendar or the range produced no events at all.
        report = format_audit_report([], [], "2025-10-01", "2025-10-02")

        assert "nothing was checked" in report
        assert "covered" not in report

    def test_a_contested_window_shows_every_candidate_it_ranked(self):
        findings = audit_findings(
            [resolution("BLAST Slam IV", 17419,
                        [candidate(17419, "Slam IV", matches=96),
                         candidate(18863, "FISSURE PLAYGROUND 2", overlap=1.0,
                                   coverage=0.4, matches=52)])],
            ledger((17419, "Slam IV", "BLAST Slam IV", LEDGER_ACTIVE)),
        )

        report = format_audit_report(findings, [])

        assert "Contested windows" in report
        assert "won " in report
        assert "FISSURE PLAYGROUND 2" in report

    def test_a_disagreement_names_both_leagues(self):
        findings = audit_findings(
            [resolution("BLAST Slam IV", 18863)],
            ledger((17419, "Slam IV", "BLAST Slam IV", LEDGER_ACTIVE),
                   (18863, "FISSURE PLAYGROUND 2", None, LEDGER_PENDING)),
        )

        report = format_audit_report(findings, [])

        assert "ledger says 17419, this run says 18863" in report

    def test_a_tracked_league_no_event_claims_is_named_with_its_reason(self):
        report = format_audit_report(
            audit_findings([resolution("BLAST Slam IV", 17419)],
                           ledger((17419, "Slam IV", "BLAST Slam IV",
                                   LEDGER_ACTIVE))),
            audit_tracked_leagues(
                ledger((19719, "The International 2026", None, LEDGER_ACTIVE)),
                [{"name": "BLAST Slam IV"}],
            ),
        )

        assert "The International 2026 — no Tier 1 event resolved to it" in report


class TestWhatTheAuditLeavesForAHuman:
    """
    The audit's last word is an instruction, not an action. Turning a verdict is
    the only thing in the whole feature that changes what gets fetched, and it
    stays Wade's edit.
    """

    def test_a_league_awaiting_a_verdict_is_spelled_out_as_the_edit_to_make(self):
        steps = format_audit_next_steps(audit_findings(
            [resolution("1win Essence II", 20009, start="2026-07-30",
                        end="2026-08-05", name="1win Essence II")],
            ledger((20009, "1win Essence II", None, LEDGER_PENDING)),
        ))

        assert "data/leagues.json" in steps
        assert "20009" in steps
        assert "2026-07-30 to 2026-08-05" in steps

    def test_a_covered_event_needs_no_decision(self):
        steps = format_audit_next_steps(audit_findings(
            [resolution("BLAST Slam IV", 17419)],
            ledger((17419, "Slam IV", "BLAST Slam IV", LEDGER_ACTIVE)),
        ))

        assert steps == "Nothing is waiting on a decision."

    def test_a_gap_is_not_a_decision_either(self):
        # There is no league to approve. A gap is OpenDota having no data, and
        # no edit to `leagues.json` fixes that.
        steps = format_audit_next_steps(
            audit_findings([resolution("PGL Wallachia Season 9", None)], ledger())
        )

        assert steps == "Nothing is waiting on a decision."

    def test_a_declined_league_is_not_proposed_again(self):
        # That is what a verdict is for. It is reported in the audit's own
        # section so the decision can be reread, and it is not an action item.
        steps = format_audit_next_steps(audit_findings(
            [resolution("The International 2025", 18324)],
            ledger((18324, "The International 2025", None, LEDGER_REJECTED)),
        ))

        assert steps == "Nothing is waiting on a decision."


class TestThe2025BackCatalogue:
    """
    The acceptance criterion, run over recorded data: every Tier 1 event from
    the dataset's first match to the end of 2025, resolved from the leagues that
    actually played inside those windows.

    The candidate pool is the shortlist the live audit's pro match walk
    produced, each league re-read in full — which is what `confirm_candidates`
    does before anything is allowed to win. It is a shortlist rather than the
    whole year's leagues, so unlike `TestThe2026Season` this does not
    demonstrate the date window doing the excluding; what it holds is the
    answer, league by league, on the half-season the 2026 fixture cannot speak
    for.

    It is worth having for one reason above the others. `TestThe2026Season`
    records a year in which no two Tier 1 events overlap, so the whole of it is
    silent on nested windows — and a nested window is what mismapped BLAST Slam
    IV on the first live run. 2025 has that case: FISSURE PLAYGROUND 2 ran
    entirely inside BLAST Slam IV's window, both are made of tracked teams, and
    the nested one played *more* matches. Six candidates fall in that window and
    only window coverage parts the top two.
    """

    @pytest.fixture(scope="class")
    @staticmethod
    def resolutions(audit_2025):
        catalogue = league_catalogue([
            {"leagueid": league["leagueid"], "name": league["name"],
             "tier": league["tier"]}
            for league in audit_2025["leagues"]
        ])
        matches = [m for league in audit_2025["leagues"]
                   for m in league["matches"]]

        return {
            record["event"]: record
            for record in resolve_tier1_events(
                audit_2025["events"],
                summarise_leagues(matches, catalogue),
                set(audit_2025["known_team_ids"]),
            )
        }

    @pytest.mark.parametrize("event_name, league_id", [
        ("BLAST Slam IV",           17419),
        ("FISSURE PLAYGROUND 2",    18863),
        ("PGL Wallachia Season 6",  18920),
        ("BLAST Slam V",            17420),
        ("DreamLeague Season 27",   18988),
    ])
    def test_each_2025_event_resolves_to_the_league_already_tracked(
        self, resolutions, event_name, league_id
    ):
        assert resolutions[event_name]["league_id"] == league_id

    def test_every_event_in_the_period_resolved(self, resolutions):
        # Five events, five leagues, no gaps. That is the audit's answer for
        # 2025 and the reason nothing was backfilled.
        assert len(resolutions) == 5
        assert all(record["league_id"] is not None
                   for record in resolutions.values())

    def test_the_nested_event_makes_its_parents_window_contested(
        self, resolutions
    ):
        # FISSURE PLAYGROUND 2 ran 23 October to 2 November, inside BLAST Slam
        # IV's 14 October to 9 November. Both are made of tracked teams, and the
        # nested one played more matches — so overlap ties and match count picks
        # the wrong league. Window coverage is the only thing that parts them.
        slam  = resolutions["BLAST Slam IV"]
        first, second = slam["candidates"][0], slam["candidates"][1]

        assert slam["ambiguous"] is True
        assert (first["league_id"], second["league_id"]) == (17419, 18863)
        assert first["overlap"] == second["overlap"]
        assert first["coverage"] > second["coverage"]
        assert first["match_count"] < second["match_count"]

    def test_the_nested_league_still_wins_its_own_window(self, resolutions):
        # The other half of the same case, and the reason coverage ranks rather
        # than gates: inside its own window FISSURE PLAYGROUND 2 covers all of
        # it, and BLAST Slam IV is not a candidate there at all because its
        # matches spill outside.
        nested = resolutions["FISSURE PLAYGROUND 2"]

        assert nested["league_id"] == 18863
        assert 17419 not in [c["league_id"] for c in nested["candidates"]]

    @pytest.mark.parametrize("league_id, name", [
        (16379, "Ancients League"),
        (17381, "European Pro League  2024-2025 Season"),
        (18618, "Snake Trophy"),
    ])
    def test_the_confirmation_pass_drops_what_the_walk_only_looked_narrow(
        self, resolutions, league_id, name
    ):
        # These three were shortlisted: as far as the pro match walk saw them,
        # their matches sat inside a Tier 1 window. Re-read in full they span
        # months — the European Pro League runs from November 2024 — and fall
        # out before anything is scored. Narrow is the direction that makes a
        # league fit inside an event it has no business winning, which is why
        # nothing may win on the walk's own summary.
        candidates = {c["league_id"]
                      for r in resolutions.values() for c in r["candidates"]}

        assert league_id not in candidates

    def test_a_qualifier_falls_inside_the_wrong_events_window_and_loses(
        self, resolutions
    ):
        # ADR-0001 says qualifiers "fall outside main event windows". Here is
        # the second counter-example after the 2026 one: BLAST Slam V's China
        # Closed Qualifier ran 20 to 26 October, inside BLAST Slam *IV*'s
        # window, and it is the team overlap that beats it — not the date.
        candidates = {c["league_id"]: c
                      for c in resolutions["BLAST Slam IV"]["candidates"]}

        assert 18830 in candidates
        assert candidates[18830]["overlap"] < candidates[17419]["overlap"]

    def test_the_audit_reports_all_five_as_covered(self, resolutions, audit_2025):
        findings = audit_findings(
            list(resolutions.values()), {"leagues": audit_2025["ledger"]}
        )

        assert [record["finding"] for record in findings] == [AUDIT_COVERED] * 5
        assert not any(record["mismatch"] for record in findings)
