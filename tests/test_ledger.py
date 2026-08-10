"""
Tests for the league ledger: loading it, filtering it to what gets fetched, and
the seeding rule that decides what counts as newly discovered.

The ledger replaces a hardcoded dict that could only say which leagues to fetch.
A dict cannot record a decision, so a league considered and rejected looked
identical to one nobody had ever looked at — which is why the Esports World
Cup's absence went unnoticed for two months. See `tier1-pipeline-automation/06`
and ADR-0001.

Everything here is pure: ledgers in, ledgers out. Reading and writing the file
is the pipeline shell's job and is not tested.
"""
import json

from core import (
    LEDGER_ACTIVE,
    LEDGER_PENDING,
    LEDGER_REJECTED,
    active_leagues,
    format_ledger,
    ledger_entries,
    ledger_problems,
    merge_discovered_leagues,
    seed_ledger,
    verdict_counts,
)


def api_league(league_id, name, tier="professional"):
    """One league as OpenDota's `/leagues` endpoint returns it."""
    return {"leagueid": league_id, "ticket": None, "banner": None,
            "tier": tier, "name": name}


def entry(league_id, name, verdict, tier1_event=None):
    """One ledger record."""
    return {"league_id": league_id, "name": name,
            "tier1_event": tier1_event, "verdict": verdict}


def ledger(*entries):
    return {"seeded_on": "2026-08-10", "leagues": list(entries)}


# The three leagues used throughout: one covered, one deliberately not, and one
# nobody has looked at yet.
COVERED  = entry(19785, "Esports World Cup 2026", LEDGER_ACTIVE)
REJECTED = entry(19364, "WAIFUS CUP SEASON 4", LEDGER_REJECTED)
UNSEEN   = entry(20100, "Some Qualifier", LEDGER_PENDING)


class TestReadingTheLedger:
    def test_entries_are_returned_in_file_order(self):
        assert ledger_entries(ledger(COVERED, REJECTED)) == [COVERED, REJECTED]

    def test_a_missing_or_malformed_ledger_reads_as_no_leagues(self):
        # The file is hand-edited, so it can be hand-broken. Every caller of
        # this either fetches nothing or discovers everything as new; neither
        # may be an exception out of the middle of a run.
        assert ledger_entries(None) == []
        assert ledger_entries({}) == []
        assert ledger_entries({"leagues": "all of them"}) == []
        assert ledger_entries(["not", "a", "ledger"]) == []


class TestVerdictFiltering:
    def test_only_active_leagues_are_fetched(self):
        fetched = active_leagues(ledger(COVERED, REJECTED, UNSEEN))

        assert fetched == {19785: "Esports World Cup 2026"}

    def test_the_name_comes_from_the_ledger_not_from_opendota(self):
        # `league_name` is written onto every match row and grouped on by the
        # dashboard. OpenDota calls league 17419 "SLAM IV"; the dataset has
        # called it "Slam IV" for 96 matches. The ledger's name is the one the
        # pipeline uses, so a curated name is not silently re-cased mid-season.
        assert active_leagues(ledger(entry(17419, "Slam IV", LEDGER_ACTIVE))) == {
            17419: "Slam IV"
        }

    def test_ledger_order_is_preserved(self):
        first  = entry(20009, "1win Essence II", LEDGER_ACTIVE)
        second = entry(17419, "Slam IV", LEDGER_ACTIVE)

        assert list(active_leagues(ledger(first, second))) == [20009, 17419]

    def test_an_entry_with_no_id_is_skipped_rather_than_fetched_as_none(self):
        assert active_leagues(ledger({"name": "Nameless", "verdict": LEDGER_ACTIVE})) == {}

    def test_an_active_league_with_no_name_still_gets_fetched(self):
        # The name is a label. Losing it costs a readable log line, not the
        # tournament — dropping the league instead would be the silent
        # exclusion this whole ledger exists to prevent.
        assert active_leagues(ledger({"league_id": 19785, "verdict": LEDGER_ACTIVE})) == {
            19785: "Unknown League"
        }

    def test_verdicts_are_counted_for_the_run_log(self):
        counts = verdict_counts(ledger(COVERED, REJECTED, UNSEEN, REJECTED))

        assert counts == {LEDGER_ACTIVE: 1, LEDGER_REJECTED: 2, LEDGER_PENDING: 1}


class TestReversibility:
    def test_flipping_a_verdict_to_active_is_the_whole_change(self):
        # The requirement, stated as a test: a rejected league becomes covered
        # by editing one word, with nothing else to touch and no re-seeding.
        before = ledger(COVERED, REJECTED)
        assert 19364 not in active_leagues(before)

        after = ledger(COVERED, {**REJECTED, "verdict": LEDGER_ACTIVE})

        assert active_leagues(after) == {
            19785: "Esports World Cup 2026",
            19364: "WAIFUS CUP SEASON 4",
        }

    def test_flipping_an_active_league_to_rejected_stops_the_fetch(self):
        assert active_leagues(ledger({**COVERED, "verdict": LEDGER_REJECTED})) == {}


class TestHandEditGuard:
    """
    The ledger is edited in pull requests, so it can arrive misspelled. A typo'd
    verdict fails the same way a rejection does — the league is not fetched —
    and that is precisely the silent exclusion the ledger exists to end. These
    problems are reported so the run says what it could not read.
    """

    def test_a_correct_ledger_has_no_problems(self):
        assert ledger_problems(ledger(COVERED, REJECTED, UNSEEN)) == []

    def test_an_unrecognised_verdict_is_reported(self):
        problems = ledger_problems(ledger(entry(19785, "EWC 2026", "Active")))

        assert len(problems) == 1
        assert "19785" in problems[0]
        assert "Active" in problems[0]

    def test_a_missing_id_is_reported(self):
        problems = ledger_problems(ledger({"name": "Nameless", "verdict": LEDGER_ACTIVE}))

        assert len(problems) == 1
        assert "league_id" in problems[0]

    def test_a_duplicated_league_id_is_reported(self):
        # Two entries for one league means one of them is being ignored, and
        # which one depends on order. A hand-added active entry sitting below a
        # rejected one would look like it had been merged and do nothing.
        problems = ledger_problems(ledger(COVERED, {**COVERED, "verdict": LEDGER_REJECTED}))

        assert len(problems) == 1
        assert "19785" in problems[0]

    def test_a_duplicate_resolves_to_the_first_entry(self):
        # Stated so the reported problem is actionable: the first wins.
        duplicated = ledger(COVERED, {**COVERED, "verdict": LEDGER_REJECTED})

        assert active_leagues(duplicated) == {19785: "Esports World Cup 2026"}


class TestSeeding:
    """
    Seeding is by existence. Every league OpenDota already knows about is
    decided at seed time, so only ids appearing *afterwards* can be pending.

    Not by date and not by id range: The International 2013 is league 65006,
    above every 2026 league, so any id cutoff mis-seeds the back catalogue.
    """

    def test_every_league_in_the_response_is_decided(self):
        seeded = seed_ledger(
            [api_league(17419, "SLAM IV"), api_league(19364, "WAIFUS CUP SEASON 4")],
            active_names={17419: "Slam IV"},
            seeded_on="2026-08-10",
        )

        assert [e["verdict"] for e in ledger_entries(seeded)] == [
            LEDGER_ACTIVE, LEDGER_REJECTED
        ]
        assert verdict_counts(seeded) == {LEDGER_ACTIVE: 1, LEDGER_REJECTED: 1}

    def test_the_covered_leagues_are_written_first(self):
        # The file holds ten thousand rejections. Opening it should answer
        # "what does this project fetch" without a search.
        seeded = seed_ledger(
            [api_league(10, "Rejected One"), api_league(20, "Covered"),
             api_league(30, "Rejected Two")],
            active_names={20: "Covered"},
            seeded_on="2026-08-10",
        )

        assert [e["league_id"] for e in ledger_entries(seeded)] == [20, 10, 30]

    def test_nothing_is_seeded_as_pending(self):
        # The whole point: after seeding, `pending` means "appeared since", and
        # it cannot mean that if seeding leaves thousands of leagues pending.
        seeded = seed_ledger([api_league(i, f"League {i}") for i in range(50)],
                             active_names={}, seeded_on="2026-08-10")

        assert LEDGER_PENDING not in verdict_counts(seeded)

    def test_the_curated_name_wins_over_the_api_name(self):
        seeded = seed_ledger([api_league(17419, "SLAM IV")],
                             active_names={17419: "Slam IV"},
                             seeded_on="2026-08-10")

        assert active_leagues(seeded) == {17419: "Slam IV"}

    def test_a_high_id_from_2013_is_rejected_not_treated_as_new(self):
        # The trap the issue names. Seeding on an id cutoff would leave The
        # International 2013 above every 2026 league and therefore "new".
        seeded = seed_ledger(
            [api_league(65006, "The International 2013"), api_league(20009, "1win Essence II")],
            active_names={20009: "1win Essence II"},
            seeded_on="2026-08-10",
        )

        by_id = {e["league_id"]: e["verdict"] for e in ledger_entries(seeded)}

        assert by_id == {65006: LEDGER_REJECTED, 20009: LEDGER_ACTIVE}

    def test_an_active_league_missing_from_the_response_is_still_recorded(self):
        # A league the project covers must never fall out of the ledger because
        # one API call came back short. Dropping it would silently stop the
        # fetch on the next run.
        seeded = seed_ledger([api_league(19364, "WAIFUS CUP SEASON 4")],
                             active_names={19719: "The International 2026"},
                             seeded_on="2026-08-10")

        assert active_leagues(seeded) == {19719: "The International 2026"}

    def test_the_resolved_event_is_left_unset_for_the_resolver_to_fill(self):
        seeded = seed_ledger([api_league(17419, "SLAM IV")],
                             active_names={17419: "Slam IV"},
                             seeded_on="2026-08-10")

        assert ledger_entries(seeded)[0]["tier1_event"] is None

    def test_the_seed_date_is_recorded_and_not_read_from_a_clock(self):
        assert seed_ledger([], active_names={}, seeded_on="2026-08-10")["seeded_on"] == (
            "2026-08-10"
        )

    def test_a_failed_league_fetch_seeds_nothing(self):
        # An empty response is not evidence that OpenDota has no leagues, and
        # seeding on it would mark every league in existence as new tomorrow.
        assert ledger_entries(seed_ledger([], active_names={}, seeded_on="2026-08-10")) == []


class TestDiscovery:
    def test_a_league_absent_from_the_ledger_becomes_pending(self):
        merged, discovered = merge_discovered_leagues(
            ledger(COVERED, REJECTED),
            [api_league(19785, "Esports World Cup 2026"),
             api_league(20100, "1win Essence III")],
        )

        assert [e["league_id"] for e in discovered] == [20100]
        assert discovered[0]["verdict"] == LEDGER_PENDING
        assert discovered[0]["name"] == "1win Essence III"
        assert verdict_counts(merged) == {
            LEDGER_ACTIVE: 1, LEDGER_REJECTED: 1, LEDGER_PENDING: 1
        }

    def test_a_league_already_decided_is_not_rediscovered(self):
        merged, discovered = merge_discovered_leagues(
            ledger(COVERED, REJECTED),
            [api_league(19785, "EWC 2026"), api_league(19364, "waifus cup season 4")],
        )

        assert discovered == []
        assert ledger_entries(merged) == [COVERED, REJECTED]

    def test_discovery_never_overwrites_a_verdict_or_a_curated_name(self):
        # OpenDota renames leagues, and the ledger's name is what the CSV
        # carries. A rename must not re-case a season's worth of rows, and it
        # certainly must not reopen a decision.
        merged, _ = merge_discovered_leagues(
            ledger(entry(17419, "Slam IV", LEDGER_ACTIVE)),
            [api_league(17419, "SLAM IV — REBRANDED")],
        )

        assert active_leagues(merged) == {17419: "Slam IV"}

    def test_a_failed_league_fetch_discovers_nothing(self):
        merged, discovered = merge_discovered_leagues(ledger(COVERED), [])

        assert discovered == []
        assert ledger_entries(merged) == [COVERED]

    def test_discovery_leaves_the_stored_ledger_untouched(self):
        # The shell writes the file only when the merge changed something, so
        # the merge has to return a new document rather than edit in place.
        stored = ledger(COVERED)
        merged, _ = merge_discovered_leagues(stored, [api_league(20100, "New")])

        assert ledger_entries(stored) == [COVERED]
        assert len(ledger_entries(merged)) == 2

    def test_an_unchanged_ledger_merges_to_an_equal_document(self):
        # What the shell's "write only if it changed" test compares. A merge
        # that reordered or rewrote an untouched ledger would rewrite a 10,000
        # line file on every run.
        stored = ledger(COVERED, UNSEEN, REJECTED)

        merged, _ = merge_discovered_leagues(stored, [api_league(19785, "EWC 2026")])

        assert merged == stored


class TestFileFormat:
    def test_it_round_trips_through_json(self):
        original = ledger(COVERED, REJECTED)

        assert json.loads(format_ledger(original)) == original

    def test_each_league_sits_on_its_own_line(self):
        # The ledger holds every league OpenDota knows about — 10,050 of them —
        # and is reviewed in pull requests. One record per line is what makes a
        # verdict change a one-line diff instead of a five-line block.
        lines = format_ledger(ledger(COVERED, REJECTED, UNSEEN)).splitlines()
        league_lines = [line for line in lines if '"league_id"' in line]

        assert len(league_lines) == 3
        assert all(line.strip().startswith("{") for line in league_lines)

    def test_it_ends_with_a_newline(self):
        assert format_ledger(ledger(COVERED)).endswith("\n")

    def test_a_ledger_with_no_leagues_is_still_valid_json(self):
        assert json.loads(format_ledger({"leagues": []})) == {"leagues": []}
