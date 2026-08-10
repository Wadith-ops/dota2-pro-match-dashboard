"""
Tests for the coverage figures the dashboard displays.

`coverage_meta` takes match rows and a timestamp and returns the record that
gets written to `data/meta.json`. The clock is a parameter because reading it
is I/O; everything else about the figures is a pure count over the rows.
"""
from datetime import datetime, timezone

from core import coverage_meta

RUN_AT = datetime(2026, 8, 9, 14, 30, 5, tzinfo=timezone.utc)


def row(match_id, league_name, start_time):
    return {"match_id": match_id, "league_name": league_name, "start_time": start_time}


class TestCoverageMeta:
    def test_counts_matches_and_tournaments(self):
        rows = [
            row(1, "1win Essence II", 1785416889),
            row(2, "1win Essence II", 1785330000),
            row(3, "DreamLeague Season 29", 1778699162),
        ]

        meta = coverage_meta(rows, RUN_AT)

        assert meta["match_count"] == 3
        assert meta["tournament_count"] == 2

    def test_reports_the_latest_match_date_not_the_run_date(self):
        # The staleness signal that matters: a run fetching nothing still
        # refreshes generated_at, so only the match date reveals a gap.
        rows = [
            row(1, "Slam IV", 1762671704),   # 2025-11-09
            row(2, "Slam IV", 1760639646),   # 2025-10-16
        ]

        meta = coverage_meta(rows, RUN_AT)

        assert meta["latest_match_date"] == "2025-11-09"
        assert meta["generated_at"] == "2026-08-09T14:30:05+00:00"

    def test_counts_the_rows_excluded_from_the_averages(self):
        # The field read null while suspect classification did not exist, since
        # 0 would have claimed "computed, none found". It is a real count now;
        # what makes a row suspect is covered in test_suspect.py.
        rows = [
            row(1, "Slam IV", 1762671704),
            {**row(2, "Slam IV", 1762671704), "is_suspect": True},
        ]

        meta = coverage_meta(rows, RUN_AT)

        assert meta["match_count"] == 2
        assert meta["excluded_count"] == 1

    def test_handles_an_empty_dataset(self):
        meta = coverage_meta([], RUN_AT)

        assert meta["match_count"] == 0
        assert meta["tournament_count"] == 0
        assert meta["latest_match_date"] is None

    def test_ignores_matches_with_no_start_time(self):
        rows = [
            row(1, "Slam IV", 1762671704),
            row(2, "Slam IV", None),
        ]

        meta = coverage_meta(rows, RUN_AT)

        assert meta["match_count"] == 2
        assert meta["latest_match_date"] == "2025-11-09"

    def test_reports_the_same_keys_the_dashboard_reads(self):
        meta = coverage_meta([row(1, "Slam IV", 1762671704)], RUN_AT)

        assert set(meta) == {
            "generated_at",
            "match_count",
            "tournament_count",
            "excluded_count",
            "latest_match_date",
        }
