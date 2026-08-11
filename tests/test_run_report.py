"""
What a run says it did.

For two months the scheduled job's only record of a pipeline run was the last
line of its stdout, which is a column name: every entry read `Pipeline:
first_blood_time_mins`. A run that fetched nothing, a run that fetched a
tournament and a run whose every league failed all logged the same thing, so
the log answered no question anybody would ask it.

These are the functions that replace it. They are pure — counters in, text out
— so the shape of the account a run gives is asserted here rather than read off
a terminal after the fact.
"""
import core


def record(name, league_id, **counts):
    """One league's line of the run, with everything unstated at zero."""
    return dict(core.league_run_record(league_id, name), **counts)


class TestFoldingTheRunIntoTotals:
    def test_matches_fetched_are_the_three_verdicts_together(self):
        totals = core.summarise_run([
            record("Slam IV", 17419, complete=4, held=1, unparsed=2),
        ])

        # Held and unparsed matches *were* fetched — they cost a call and they
        # are in the store. What differs is whether they will be fetched again.
        assert totals["fetched"] == 7
        assert (totals["complete"], totals["held"], totals["unparsed"]) == (4, 1, 2)

    def test_the_leagues_are_counted_including_the_ones_that_failed(self):
        totals = core.summarise_run([
            record("Slam IV", 17419, available=96, skipped=96),
            record("DreamLeague S29", 18324, list_failed=True),
        ])

        assert totals["leagues"] == 2
        assert totals["leagues_failed"] == 1

    def test_matches_already_had_are_counted_apart_from_matches_fetched(self):
        # The denominator of "nothing new to fetch". Without it a quiet run and
        # a run that lost its checkpoint look identical.
        totals = core.summarise_run([
            record("Slam IV", 17419, available=96, skipped=92, complete=4),
        ])

        assert totals["skipped"] == 92
        assert totals["available"] == 96

    def test_a_run_over_no_leagues_totals_to_zero_rather_than_nothing(self):
        totals = core.summarise_run([])

        assert totals["leagues"] == 0
        assert totals["fetched"] == 0
        assert totals["failed"] == 0


class TestTheOneLineTheScheduledJobLogs:
    def test_it_is_prefixed_so_the_job_can_find_it_in_the_output(self):
        line = core.format_run_summary(core.summarise_run([]))

        assert line.startswith(core.RUN_SUMMARY_PREFIX)

    def test_a_quiet_run_says_it_fetched_nothing_and_that_nothing_failed(self):
        # The distinction the log could not make: a run with no new matches is
        # the normal state of a six-hourly job, and it must not read like a
        # run that fell over.
        line = core.format_run_summary(core.summarise_run([
            record("Slam IV", 17419, available=96, skipped=96),
        ]))

        assert "0 matches fetched" in line
        assert "no failures" in line

    def test_a_run_that_fetched_matches_says_how_many_and_of_what_kind(self):
        line = core.format_run_summary(core.summarise_run([
            record("Slam IV", 17419, available=96, skipped=92,
                   complete=3, held=1),
        ]))

        assert "4 matches fetched" in line
        assert "3 complete" in line
        assert "1 held" in line

    def test_a_failed_league_list_is_named_in_the_line(self):
        line = core.format_run_summary(core.summarise_run([
            record("DreamLeague S29", 18324, list_failed=True),
        ]))

        assert "no failures" not in line
        assert "1 league list failed" in line

    def test_failed_match_fetches_are_named_in_the_line(self):
        line = core.format_run_summary(core.summarise_run([
            record("Slam IV", 17419, available=96, skipped=92, complete=2,
                   failed=2),
        ]))

        assert "no failures" not in line
        assert "2 match fetches failed" in line

    def test_it_is_one_line(self):
        # The job logs it verbatim, one entry per run.
        line = core.format_run_summary(core.summarise_run([
            record("Slam IV", 17419, failed=1, list_failed=True),
        ]))

        assert "\n" not in line


class TestFindingTheSummaryInTheOutput:
    """
    What `auto_update.py` logs. It takes the summary line rather than the last
    line of stdout, which is what it used to do — and the last line of a
    successful run belongs to whatever happened to print last.
    """

    def test_the_summary_is_found_wherever_it_sits_in_the_output(self):
        summary = core.format_run_summary(core.summarise_run([]))
        output  = f"Flattening 1822 matches...\n{summary}\nCSV saved to data/x.csv"

        assert core.read_run_summary(output) == summary

    def test_a_run_that_printed_no_summary_reads_as_none(self):
        # Which is how the job tells a run that finished quietly from a run
        # that stopped somewhere in the middle of itself.
        assert core.read_run_summary("Flattening 1822 matches...\nfirst_blood_time_mins") is None

    def test_no_output_at_all_reads_as_none(self):
        assert core.read_run_summary("") is None
        assert core.read_run_summary(None) is None

    def test_the_last_summary_wins(self):
        first  = f"{core.RUN_SUMMARY_PREFIX}an older run quoted in the output"
        latest = core.format_run_summary(core.summarise_run([]))

        assert core.read_run_summary(f"{first}\n{latest}") == latest


class TestThePerLeagueAccount:
    def test_every_league_is_named_with_its_counts(self):
        report = core.format_run_report([
            record("Slam IV", 17419, available=96, skipped=92, complete=4),
            record("1win Essence II", 20009, available=60, skipped=60),
        ])

        assert "Slam IV" in report
        assert "1win Essence II" in report
        assert "96" in report

    def test_a_league_whose_match_list_failed_says_so_rather_than_reading_empty(
        self,
    ):
        # A league with no matches and a league that could not be reached are
        # different facts, and the second is the one worth acting on. Reporting
        # both as `0` is how the Esports World Cup went missing for two months.
        report = core.format_run_report([
            record("The International 2026", 19719, list_failed=True),
        ])

        assert "The International 2026" in report
        assert "failed" in report.lower()

    def test_a_run_over_no_leagues_reports_that_and_not_an_empty_table(self):
        report = core.format_run_report([])

        assert report.strip()
