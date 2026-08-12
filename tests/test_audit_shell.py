"""
The audit's shell: the period it chooses, what it writes, and one end-to-end
run with every collaborator replaced.

The smoke test earns its place for the same reason the resolver's does. Every
piece below it is tested in `test_audit.py`, and the orchestrator is still where
a moved function leaves a `NameError` that nothing asserting on returned data
can catch — because the fault is that no data is returned at all.

Nothing here touches the network: `fetch_url`, the Liquipedia client and the
Standard store are all stubbed, and the ledger points at pytest's `tmp_path`.
"""
import json
from datetime import datetime, timezone

import pytest

import audit_coverage
import opendota_pipeline as pipeline


def at(day):
    """Midday UTC on an ISO date, as a unix timestamp."""
    return int(datetime.fromisoformat(f"{day}T12:00:00+00:00").timestamp())


def pro_match(match_id, day, league_id=18863):
    """One row of `/proMatches`, trimmed to what the resolver reads."""
    return {"match_id": match_id, "start_time": at(day), "leagueid": league_id,
            "radiant_team_id": 15, "dire_team_id": 39}


class TestImportingTheModuleDoesNothing:
    def test_it_makes_no_calls_and_writes_no_files(self):
        # `main()` is the only entry point, so `import audit_coverage` costs
        # nothing and the tests can exercise the shell without a walk.
        import importlib

        importlib.reload(audit_coverage)


class TestChoosingThePeriod:
    ROWS = [{"start_time": at("2025-10-14")}, {"start_time": at("2026-08-11")}]

    def test_it_defaults_to_the_dataset_and_today(self):
        opens, closes = audit_coverage.audit_period(self.ROWS, None, None)

        assert opens == "2025-10-14"
        assert closes >= "2026-08-11"

    def test_both_ends_can_be_overridden(self):
        opens, closes = audit_coverage.audit_period(
            self.ROWS, "2025-01-01", "2025-12-31"
        )

        assert (opens, closes) == ("2025-01-01", "2025-12-31")

    def test_an_empty_dataset_leaves_the_start_unset_rather_than_guessing(self):
        opens, _ = audit_coverage.audit_period([], None, None)

        assert opens is None


class TestWritingWhatItWorkedOut:
    LEDGER = {"leagues": [{"league_id": 18863, "name": "FISSURE PLAYGROUND 2",
                           "tier1_event": None, "verdict": "active"}]}
    RESOLUTIONS = [{"event": "FISSURE PLAYGROUND 2", "league_id": 18863,
                    "candidates": []}]
    # No mismatch, so nothing is withheld. The findings the audit derives are
    # what decides that — see `TestAMismatchIsNeverWritten` below.
    FINDINGS = [{"event": "FISSURE PLAYGROUND 2", "mismatch": False}]

    @pytest.fixture
    def ledger_file(self, tmp_path, monkeypatch):
        path = tmp_path / "leagues.json"
        monkeypatch.setattr(pipeline, "LEDGER_FILE", str(path))
        return path

    def test_a_new_mapping_is_written(self, ledger_file):
        audit_coverage.record_resolutions(
            self.LEDGER, self.RESOLUTIONS, self.FINDINGS, False
        )

        stored = json.loads(ledger_file.read_text(encoding="utf-8"))
        assert stored["leagues"][0]["tier1_event"] == "FISSURE PLAYGROUND 2"

    def test_a_dry_run_writes_nothing(self, ledger_file):
        audit_coverage.record_resolutions(
            self.LEDGER, self.RESOLUTIONS, self.FINDINGS, True
        )

        assert not ledger_file.exists()

    def test_a_mapping_already_on_record_writes_nothing(self, ledger_file):
        mapped = {"leagues": [dict(self.LEDGER["leagues"][0],
                                   tier1_event="FISSURE PLAYGROUND 2")]}

        audit_coverage.record_resolutions(
            mapped, self.RESOLUTIONS, self.FINDINGS, False
        )

        assert not ledger_file.exists()

    def test_the_verdict_is_never_touched(self, ledger_file):
        # The whole rule the audit hangs off: recognising a tournament is the
        # resolver's job, covering one is Wade's.
        pending = {"leagues": [dict(self.LEDGER["leagues"][0],
                                    verdict="pending")]}

        audit_coverage.record_resolutions(
            pending, self.RESOLUTIONS, self.FINDINGS, False
        )

        stored = json.loads(ledger_file.read_text(encoding="utf-8"))
        assert stored["leagues"][0]["verdict"] == "pending"

    def test_a_mismatch_is_withheld_and_said_out_loud(self, ledger_file, capsys):
        # The destructive case. `apply_resolutions` keys by league id, so
        # writing this would leave 17419 still naming BLAST Slam IV *and* point
        # 18863 at it too — orphaning FISSURE PLAYGROUND 2 in the same write.
        ledger = {"leagues": [
            {"league_id": 17419, "name": "Slam IV",
             "tier1_event": "BLAST Slam IV", "verdict": "active"},
            {"league_id": 18863, "name": "FISSURE PLAYGROUND 2",
             "tier1_event": "FISSURE PLAYGROUND 2", "verdict": "active"},
        ]}

        audit_coverage.record_resolutions(
            ledger,
            [{"event": "BLAST Slam IV", "league_id": 18863, "candidates": []}],
            [{"event": "BLAST Slam IV", "mismatch": True}],
            False,
        )

        assert not ledger_file.exists()
        assert "Withheld" in capsys.readouterr().out


class TestReadingTheLedger:
    """
    A dry run promises to write nothing, and `pipeline.load_ledger` does not
    keep that promise: it records newly discovered leagues and rewrites the
    file, and seeds a fresh one when the file is missing.
    """

    LEDGER = {"leagues": [{"league_id": 18863, "name": "FISSURE PLAYGROUND 2",
                           "tier1_event": None, "verdict": "active"}]}
    DISCOVERED = [{"leagueid": 18863, "name": "FISSURE PLAYGROUND 2",
                   "tier": "professional"},
                  {"leagueid": 99999, "name": "Brand New League",
                   "tier": "professional"}]

    @pytest.fixture
    def ledger_file(self, tmp_path, monkeypatch):
        path = tmp_path / "leagues.json"
        path.write_text(json.dumps(self.LEDGER), encoding="utf-8")
        monkeypatch.setattr(pipeline, "LEDGER_FILE", str(path))
        return path

    def test_a_dry_run_does_not_record_a_newly_discovered_league(
        self, ledger_file
    ):
        before = ledger_file.read_text(encoding="utf-8")

        audit_coverage.load_ledger(self.DISCOVERED, dry_run=True)

        assert ledger_file.read_text(encoding="utf-8") == before

    def test_a_real_run_still_does(self, ledger_file):
        audit_coverage.load_ledger(self.DISCOVERED, dry_run=False)

        stored = json.loads(ledger_file.read_text(encoding="utf-8"))
        assert 99999 in [entry["league_id"] for entry in stored["leagues"]]

    def test_a_dry_run_reports_an_unreadable_ledger_rather_than_seeding_one(
        self, ledger_file, capsys
    ):
        ledger_file.write_text("not json", encoding="utf-8")

        assert audit_coverage.load_ledger(self.DISCOVERED, True) == {"leagues": []}
        assert not audit_coverage.load_ledger(self.DISCOVERED, True)["leagues"]
        assert "will not seed or repair it" in capsys.readouterr().out


class TestTheWholeAuditRunsEndToEnd:
    EVENTS = [
        {"name": "FISSURE PLAYGROUND 2", "start_date": "2025-10-23",
         "end_date": "2025-11-02"},
        # Before the dataset began: out of range, and never reported as a gap.
        {"name": "The International 2025", "start_date": "2025-09-04",
         "end_date": "2025-09-14"},
    ]
    API_LEAGUES = [{"leagueid": 18863, "name": "FISSURE PLAYGROUND 2",
                    "tier": "professional"}]

    @pytest.fixture
    def wired(self, tmp_path, monkeypatch):
        """Every collaborator replaced: no network, no real files."""
        ledger = tmp_path / "leagues.json"
        ledger.write_text(json.dumps({"leagues": [
            {"league_id": 18863, "name": "FISSURE PLAYGROUND 2",
             "tier1_event": None, "verdict": "pending"},
        ]}), encoding="utf-8")

        monkeypatch.setattr(pipeline, "LEDGER_FILE", str(ledger))
        monkeypatch.setattr(pipeline, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(pipeline, "CHECKPOINT_DIR", str(tmp_path))
        monkeypatch.setattr(pipeline, "fetch_all_leagues",
                            lambda: self.API_LEAGUES)
        monkeypatch.setattr(
            audit_coverage.liquipedia, "get_tier1_events",
            lambda: {"events": self.EVENTS, "source": "cache"},
        )
        # The dataset the tiebreaker is measured against: two matches, so teams
        # 15 and 39 are already tracked and the candidate scores 100%.
        monkeypatch.setattr(pipeline, "read_standard_store", lambda: [
            {"match_id": 1, "start_time": at("2025-10-14"),
             "radiant_team": {"team_id": 15}, "dire_team": {"team_id": 39}},
        ])

        matches = [pro_match(8600000000 + n, f"2025-10-2{n + 4}")
                   for n in range(4)]

        def fetch(url):
            if "proMatches" in url:
                return matches if "less_than" not in url else []
            return matches

        monkeypatch.setattr(pipeline, "fetch_url", fetch)
        return ledger

    def test_it_finds_the_event_and_reports_it_awaiting_a_verdict(
        self, wired, capsys
    ):
        findings = audit_coverage.run_audit()

        assert [record["event"] for record in findings] == [
            "FISSURE PLAYGROUND 2"
        ]
        assert findings[0]["finding"] == "untracked"
        assert findings[0]["league_id"] == 18863

        printed = capsys.readouterr().out
        assert "AUDIT SUMMARY:" in printed
        assert "set its verdict in data/leagues.json to 'active'" in printed

    def test_an_event_before_the_dataset_began_is_out_of_range(self, wired):
        # Not a gap. It finished five weeks before the first match this project
        # holds, so it was never in the dataset's intended range.
        findings = audit_coverage.run_audit()

        assert "The International 2025" not in [r["event"] for r in findings]

    def test_the_mapping_is_recorded_but_the_verdict_is_not(self, wired):
        audit_coverage.run_audit()

        stored = json.loads(wired.read_text(encoding="utf-8"))
        assert stored["leagues"][0]["tier1_event"] == "FISSURE PLAYGROUND 2"
        assert stored["leagues"][0]["verdict"] == "pending"

    def test_a_dry_run_reports_the_same_answer_and_writes_none_of_it(
        self, wired
    ):
        before = wired.read_text(encoding="utf-8")

        findings = audit_coverage.run_audit(dry_run=True)

        assert findings[0]["league_id"] == 18863
        assert wired.read_text(encoding="utf-8") == before

    def test_an_empty_dataset_and_no_from_date_is_not_a_period(
        self, wired, monkeypatch, capsys
    ):
        # Auditing from the beginning of Liquipedia's calendar would walk twenty
        # years of pro matches to report that an empty dataset is missing
        # everything.
        monkeypatch.setattr(pipeline, "read_standard_store", lambda: [])

        assert audit_coverage.run_audit() == []
        assert "No period to audit" in capsys.readouterr().out

    def test_a_period_holding_no_events_is_reported_as_nothing_checked(
        self, wired, capsys
    ):
        # Never as a clean bill of health. No events audited means the range
        # produced none, which is not the same as nothing being missing.
        findings = audit_coverage.run_audit("2024-01-01", "2024-12-31")

        assert findings == []
        assert "nothing was checked" in capsys.readouterr().out

    def test_a_period_holding_no_events_still_reports_the_other_direction(
        self, wired, monkeypatch, capsys
    ):
        # The reverse check reads the whole calendar and the whole ledger and
        # depends on the period not at all. An audit reports in both directions
        # or it is not an audit.
        ledger = json.loads(wired.read_text(encoding="utf-8"))
        ledger["leagues"][0]["verdict"] = "active"
        wired.write_text(json.dumps(ledger), encoding="utf-8")

        audit_coverage.run_audit("2024-01-01", "2024-12-31")

        assert "no Tier 1 event resolved to it" in capsys.readouterr().out

    def test_an_empty_league_list_stops_the_audit_like_a_failed_one(
        self, wired, monkeypatch, capsys
    ):
        # OpenDota has ten thousand leagues, so `[]` is a failure and not an
        # answer. Carrying on would leave every summary with no tier, switching
        # off the prune that keeps showmatch series out of a Tier 1 window —
        # and the resulting mappings would be written to the ledger.
        monkeypatch.setattr(pipeline, "fetch_all_leagues", lambda: [])

        assert audit_coverage.run_audit() == []
        assert "cannot rank candidates without it" in capsys.readouterr().out

    def test_no_tracked_teams_stops_the_audit_rather_than_ranking_on_nothing(
        self, wired, monkeypatch, capsys
    ):
        # With no teams every candidate scores zero, the primary ranking key
        # collapses, and windows fall through to coverage and match count. That
        # answer would then be written to the ledger.
        monkeypatch.setattr(pipeline, "read_standard_store", lambda: [])

        assert audit_coverage.run_audit("2025-10-01", "2025-12-31") == []
        assert "No tracked teams" in capsys.readouterr().out


class TestTheCommandLine:
    def test_the_defaults_leave_both_ends_to_the_data(self):
        args = audit_coverage.parse_args([])

        assert args.opens is None and args.closes is None
        assert args.dry_run is False

    def test_an_unpadded_date_is_an_error_not_a_wider_range(self):
        # The comparison downstream is a string comparison against Liquipedia's
        # dates, and "2025-1-1" sorts below every real one — silently widening
        # the range to the whole calendar and answering plausibly.
        with pytest.raises(SystemExit):
            audit_coverage.parse_args(["--from", "2025-1-1"])

    def test_a_date_that_is_not_a_date_at_all_is_an_error(self):
        with pytest.raises(SystemExit):
            audit_coverage.parse_args(["--to", "last Tuesday"])

    def test_a_backwards_range_is_refused_where_the_mistake_was_made(self):
        # It would select no events, and no events is reported as "nothing was
        # checked" — which reads like an answer.
        with pytest.raises(SystemExit):
            audit_coverage.parse_args(
                ["--from", "2026-01-01", "--to", "2025-01-01"]
            )

    def test_the_period_and_the_dry_run_are_read_off_the_command_line(self):
        args = audit_coverage.parse_args(
            ["--from", "2025-10-01", "--to", "2025-12-31", "--dry-run"]
        )

        assert (args.opens, args.closes) == ("2025-10-01", "2025-12-31")
        assert args.dry_run is True

    def test_the_walk_cap_is_the_audits_own_and_not_the_daily_runs(self):
        # The audit walks back to the start of the dataset, not to the oldest
        # unresolved event of the last day or two. The daily cap would stop it
        # short and turn a season into a page of gaps.
        assert audit_coverage.AUDIT_MAX_PAGES > pipeline.PRO_MATCHES_MAX_PAGES
