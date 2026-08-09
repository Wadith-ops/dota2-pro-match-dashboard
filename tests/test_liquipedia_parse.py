"""
Tests for the Liquipedia Tier 1 table parser.

Expected values are read off the recorded page by hand. The fixture is the
whole `action=parse` envelope, recorded 2026-08-09; nothing here touches the
network.

The parser reads the *rendered tournaments table*. It must not pick up the
Timeline template, which deliberately lists Tier 2 events by the same
organisers — see the note in tier1-pipeline-automation/07.
"""
import pytest

from core import parse_date_range, parse_tier1_events, events_in_year


class TestParseDateRange:
    def test_reads_a_range_inside_one_month(self):
        # "Oct 19–31, 2027" — en-dash, no spaces around it.
        assert parse_date_range("Oct 19–31, 2027") == ("2027-10-19", "2027-10-31")

    def test_reads_a_range_that_crosses_a_month(self):
        # "Sep 29 – Oct 11, 2026" — en-dash with spaces, month repeated.
        assert parse_date_range("Sep 29 – Oct 11, 2026") == (
            "2026-09-29",
            "2026-10-11",
        )

    def test_reads_a_single_day_event(self):
        # BetBoom Dacha Dubai 2024: 1x1 is a one-day show match. Start and end
        # are the same date rather than the end being absent.
        assert parse_date_range("Feb 10, 2024") == ("2024-02-10", "2024-02-10")

    def test_reads_a_range_that_spells_out_both_years(self):
        # How the page actually writes a new-year crossing: each date carries
        # its own year. All 12 such rows on the recorded page use this form.
        assert parse_date_range("Nov 28, 2014 – Jul 05, 2015") == (
            "2014-11-28",
            "2015-07-05",
        )

    def test_infers_the_earlier_year_when_one_year_is_printed(self):
        # Defensive rather than observed: no row on the recorded page prints a
        # single year across a new year — they use the both-years form above.
        # If that ever changes, the trailing year belongs to the end date, and
        # without this the event reads as running backwards by eleven months.
        assert parse_date_range("Dec 28 – Jan 05, 2013") == (
            "2012-12-28",
            "2013-01-05",
        )

    def test_tolerates_a_plain_hyphen_and_non_breaking_spaces(self):
        # Liquipedia's markup is not consistent about which dash or space it
        # emits, and the difference must not decide whether a row parses.
        assert parse_date_range("Mar 02-14, 2027") == (
            "2027-03-02",
            "2027-03-14",
        )

    @pytest.mark.parametrize("text", ["", "TBD", "Cancelled", "Q3 2026", None])
    def test_returns_none_rather_than_raising_on_an_unparseable_date(self, text):
        # A date Liquipedia has not settled yet must not abort the whole parse.
        assert parse_date_range(text) is None


class TestParseTier1Events:
    def test_reads_every_row_of_the_rendered_table(self, liquipedia_tier1_html):
        # 23 year tables, 324 body rows, counted from the recorded page.
        events = parse_tier1_events(liquipedia_tier1_html)
        assert len(events) == 324

    def test_reads_name_dates_and_prize_pool_for_a_known_event(
        self, liquipedia_tier1_html
    ):
        events = parse_tier1_events(liquipedia_tier1_html)
        ti = next(e for e in events if e["name"] == "The International 2026")

        assert ti == {
            "name": "The International 2026",
            "start_date": "2026-08-13",
            "end_date": "2026-08-23",
            "prize_pool_usd": 2744104,
        }

    def test_reads_an_event_whose_dates_cross_a_month(self, liquipedia_tier1_html):
        events = parse_tier1_events(liquipedia_tier1_html)
        blast = next(e for e in events if e["name"] == "BLAST SLAM VIII")

        assert blast["start_date"] == "2026-09-29"
        assert blast["end_date"] == "2026-10-11"

    def test_returns_the_thirteen_tier1_events_for_2026(self, liquipedia_tier1_html):
        # The count issue 07 names as the correctness check for this parser.
        events = events_in_year(parse_tier1_events(liquipedia_tier1_html), 2026)

        assert [e["name"] for e in events] == [
            "BLAST SLAM IX",
            "BLAST SLAM VIII",
            "PGL Wallachia Season 9",
            "The International 2026",
            "1win Essence II",
            "Esports World Cup 2026",
            "BLAST SLAM VII",
            "DreamLeague Season 29",
            "PGL Wallachia Season 8",
            "ESL One Birmingham 2026",
            "PGL Wallachia Season 7",
            "DreamLeague Season 28",
            "BLAST SLAM VI",
        ]

    def test_includes_1win_essence_ii_and_excludes_fissure_universe_episode_8(
        self, liquipedia_tier1_html
    ):
        # The regression that motivated the "table, not timeline" note. Reading
        # the Timeline template concluded the exact opposite of both of these,
        # and that conclusion is what left a tournament out of the dataset.
        names = {e["name"] for e in parse_tier1_events(liquipedia_tier1_html)}

        assert "1win Essence II" in names
        assert "FISSURE Universe: Episode 8" not in names

        # Not a blanket exclusion of the series — Episodes 4 and 6 are on the
        # table and are genuinely Tier 1. Only Episode 8 is not, which is the
        # distinction reading the timeline lost.
        assert "FISSURE Universe: Episode 6" in names
        assert "FISSURE Universe: Episode 4" in names

    def test_every_event_has_a_name(self, liquipedia_tier1_html):
        events = parse_tier1_events(liquipedia_tier1_html)
        assert all(e["name"] for e in events)

    def test_every_event_on_the_recorded_page_has_dates(self, liquipedia_tier1_html):
        # Not a guarantee about Liquipedia in general — a row with a TBD date is
        # allowed through with None. It records that the recorded page has none,
        # so a future parse regression shows up as dates going missing.
        events = parse_tier1_events(liquipedia_tier1_html)
        undated = [e["name"] for e in events if e["start_date"] is None]
        assert undated == []

    def test_returns_nothing_for_html_without_the_table(self):
        assert parse_tier1_events("<html><body><p>nope</p></body></html>") == []

    @pytest.mark.parametrize("html", ["", None])
    def test_returns_nothing_rather_than_raising_on_empty_input(self, html):
        assert parse_tier1_events(html) == []


class TestEventsInYear:
    def test_selects_on_the_start_date(self):
        events = [
            {"name": "ends next year", "start_date": "2026-12-28", "end_date": "2027-01-05"},
            {"name": "in year", "start_date": "2026-03-01", "end_date": "2026-03-09"},
            {"name": "other year", "start_date": "2025-03-01", "end_date": "2025-03-09"},
        ]
        assert [e["name"] for e in events_in_year(events, 2026)] == [
            "ends next year",
            "in year",
        ]

    def test_skips_events_with_no_start_date(self):
        events = [{"name": "undated", "start_date": None, "end_date": None}]
        assert events_in_year(events, 2026) == []
