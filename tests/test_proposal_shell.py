"""
The script that opens the pull request — without running git or `gh` once.

`propose_coverage.run` is the seam: every command the script would have run is
recorded instead, so the order of commit, pull and push is asserted on rather
than performed, and `gh pr create` is a string in a list rather than a review
request on a public repository.

What is being held here is mostly *restraint*. The acceptance criteria that can
break a repository are the quiet ones: only one open pull request at a time, a
run that detects nothing opens none, and closing a stale proposal must not
un-cover a league somebody covered by hand in the meantime.
"""
import json
from datetime import date
from pathlib import Path

import pytest

import core
import propose_coverage

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


PENDING_EVENT = {
    "event"      : "The International 2026",
    "start_date" : "2026-08-13",
    "end_date"   : "2026-08-23",
    "league_id"  : 19719,
    "league_name": "The International 2026",
    "verdict"    : core.LEDGER_PENDING,
    "ambiguous"  : False,
    "candidates" : [{"league_id": 19719, "name": "The International 2026",
                     "overlap": 1.0, "coverage": 1.0, "match_count": 49}],
    "pull_request": None,
}

SECOND_EVENT = dict(
    PENDING_EVENT,
    event="1win Essence II", start_date="2026-07-30", end_date="2026-08-05",
    league_id=20009, league_name="1win Essence II",
    candidates=[{"league_id": 20009, "name": "1win Essence II",
                 "overlap": 1.0, "coverage": 1.0, "match_count": 60}],
)

LEDGER = {
    "_comment": "Every league OpenDota knows about.",
    "leagues" : [
        {"league_id": 19719, "name": "The International 2026",
         "tier1_event": "The International 2026",
         "verdict": core.LEDGER_PENDING},
        {"league_id": 20009, "name": "1win Essence II",
         "tier1_event": "1win Essence II", "verdict": core.LEDGER_PENDING},
        {"league_id": 17119, "name": "DreamLeague Season 29",
         "tier1_event": None, "verdict": core.LEDGER_ACTIVE},
    ],
}

PR_URL = "https://github.com/Wadith-ops/dota2-pro-match-dashboard/pull/7"


class Result:
    def __init__(self, stdout="", returncode=0):
        self.stdout     = stdout
        self.stderr     = ""
        self.returncode = returncode


@pytest.fixture
def shell(tmp_path, monkeypatch):
    """
    Replaces `propose_coverage.run` and points the script's files at `tmp_path`.

    Assign `shell.open_pull_requests` to what `gh pr list` should answer.
    Every command lands on `shell.commands`.
    """
    class Stub:
        def __init__(self):
            self.commands           = []
            self.open_pull_requests = []
            self.branch             = "master"
            self.failing            = None
            self.created            = PR_URL + "\n"

        def run(self, cmd, check=True):
            self.commands.append([str(part) for part in cmd])

            if self.failing and cmd[:len(self.failing)] == self.failing:
                if check:
                    raise SystemExit(1)
                return Result("", 1)

            if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
                return Result(self.branch + "\n")
            if cmd[:3] == ["gh", "pr", "list"]:
                return Result(json.dumps(self.open_pull_requests))
            if cmd[:3] == ["gh", "pr", "create"]:
                return Result(self.created)

            return Result()

        def ran(self, *prefix):
            return [cmd for cmd in self.commands
                    if cmd[:len(prefix)] == list(prefix)]

        @property
        def ledger(self):
            return json.loads(
                (tmp_path / "data" / "leagues.json").read_text(encoding="utf-8")
            )

        @property
        def resolution(self):
            return json.loads(
                (tmp_path / "data" / "tier1_resolution.json")
                .read_text(encoding="utf-8")
            )

    stub = Stub()
    (tmp_path / "data").mkdir()

    monkeypatch.setattr(propose_coverage, "run", stub.run)
    monkeypatch.setattr(propose_coverage, "HERE", tmp_path)
    monkeypatch.setattr(propose_coverage, "LEDGER_FILE",
                        tmp_path / "data" / "leagues.json")
    monkeypatch.setattr(propose_coverage, "RESOLUTION_FILE",
                        tmp_path / "data" / "tier1_resolution.json")

    return stub


def given(shell, events, ledger=None):
    """Writes the two files the script reads."""
    propose_coverage.LEDGER_FILE.write_text(
        json.dumps(ledger or LEDGER), encoding="utf-8"
    )
    propose_coverage.RESOLUTION_FILE.write_text(
        json.dumps({"generated_at": "2026-08-14T09:00:59+00:00",
                    "event_count": len(events), "events": events}),
        encoding="utf-8",
    )


# ── A run with nothing to say ─────────────────────────────────────────────────


class TestSilence:
    def test_nothing_awaiting_a_verdict_runs_no_command_at_all(self, shell):
        # "A run that detects nothing new opens no PR and makes no noise." Not
        # one `gh` call either: this runs every six hours and steady state is
        # nothing to propose.
        given(shell, [dict(PENDING_EVENT, verdict=core.LEDGER_ACTIVE)])

        assert propose_coverage.propose() == 0
        assert shell.commands == []

    def test_a_gap_is_not_a_proposal(self, shell):
        given(shell, [dict(PENDING_EVENT, league_id=None, league_name=None,
                           verdict=None, candidates=[])])

        assert propose_coverage.propose() == 0
        assert shell.commands == []

    def test_a_missing_resolution_file_is_not_a_crash(self, shell):
        # The dashboard degrades when a side file is missing; so does this.
        assert propose_coverage.propose() == 0
        assert shell.commands == []

    def test_a_dry_run_writes_nothing_and_asks_nothing(self, shell):
        given(shell, [PENDING_EVENT])

        assert propose_coverage.propose(dry_run=True) == 0
        assert shell.commands == []


class TestWhatTheBodySaysAboutGaps:
    def test_an_overdue_gap_is_reported_and_an_upcoming_one_is_not(
            self, shell, capsys):
        # A tournament being played that this project has no data for is the
        # loudest news the resolver has. One three months away, repeated in
        # every proposal, is how a real gap stops being read.
        given(shell, [
            PENDING_EVENT,
            dict(PENDING_EVENT, event="Under way already",
                 start_date="2026-08-10", end_date="2026-08-20",
                 league_id=None, league_name=None, verdict=None, candidates=[]),
            dict(PENDING_EVENT, event="Months off yet",
                 start_date="2026-11-17", end_date="2026-11-29",
                 league_id=None, league_name=None, verdict=None, candidates=[]),
        ])

        propose_coverage.propose(dry_run=True, today=date(2026, 8, 14))
        printed = capsys.readouterr().out

        assert "Under way already" in printed
        assert "Months off yet" not in printed


# ── Opening the pull request ──────────────────────────────────────────────────


class TestOpeningIt:
    def test_a_pending_league_opens_one(self, shell):
        given(shell, [PENDING_EVENT])

        assert propose_coverage.propose() == 0
        assert shell.ran("gh", "pr", "create")

    def test_the_branch_carries_the_active_verdict(self, shell):
        given(shell, [PENDING_EVENT])
        propose_coverage.propose()

        verdicts = {entry["league_id"]: entry["verdict"]
                    for entry in shell.ledger["leagues"]}

        assert verdicts[19719] == core.LEDGER_ACTIVE

    def test_no_other_league_is_touched(self, shell):
        given(shell, [PENDING_EVENT])
        propose_coverage.propose()

        verdicts = {entry["league_id"]: entry["verdict"]
                    for entry in shell.ledger["leagues"]}

        assert verdicts[20009] == core.LEDGER_PENDING
        assert verdicts[17119] == core.LEDGER_ACTIVE

    def test_only_the_ledger_is_staged_on_the_branch(self, shell):
        # The proposal is one line of one file. A branch carrying the CSV as
        # well would be a data push wearing a review's clothes.
        given(shell, [PENDING_EVENT])
        propose_coverage.propose()

        assert shell.ran("git", "add")[0] == ["git", "add", "data/leagues.json"]

    def test_an_unreadable_ledger_proposes_nothing(self, shell):
        # Every verdict in that file survives a syntax error and none of it
        # survives being written over — the rule `load_ledger` follows.
        given(shell, [PENDING_EVENT])
        propose_coverage.LEDGER_FILE.write_text("{ oh dear", encoding="utf-8")

        propose_coverage.propose()

        assert shell.ran("git", "checkout", "-B") == []
        assert shell.ran("gh", "pr", "create")    == []

    def test_the_branch_is_the_one_branch(self, shell):
        given(shell, [PENDING_EVENT])
        propose_coverage.propose()

        assert shell.ran("git", "checkout", "-B")[0][3] == core.PROPOSAL_BRANCH

    def test_the_run_returns_to_the_branch_it_started_on(self, shell):
        # Everything after this — recording the pull request's URL — belongs on
        # master, and a job left on a side branch pushes the wrong thing.
        given(shell, [PENDING_EVENT])
        propose_coverage.propose()

        assert shell.ran("git", "checkout", "master")

    def test_a_detached_head_is_not_where_the_run_comes_back_to(self, shell):
        # `git rev-parse --abbrev-ref HEAD` answers "HEAD" when detached, and
        # checking that out would leave the run sitting on the proposal
        # branch's commit — where the commit recording the pull request's URL
        # would be stranded, because it is pushed to master by name.
        given(shell, [PENDING_EVENT])
        shell.branch = "HEAD"

        propose_coverage.propose()

        assert shell.ran("git", "checkout", "master")

    def test_the_ledger_is_only_ever_committed_on_the_branch(self, shell):
        # The whole point: a machine may propose `active`, and only a merge
        # makes it true. So the ledger edit lives between the two checkouts,
        # and what reaches master is the pull request's link and nothing else.
        given(shell, [PENDING_EVENT])
        propose_coverage.propose()

        order  = [" ".join(cmd) for cmd in shell.commands]
        opened = order.index(f"git checkout -B {core.PROPOSAL_BRANCH}")
        edited = order.index("git add data/leagues.json")
        closed = order.index("git checkout master")

        assert opened < edited < closed
        assert "git add data/leagues.json" not in order[closed:]


class TestOnlyOneAtATime:
    def test_the_same_proposal_twice_opens_nothing_the_second_time(self, shell):
        given(shell, [PENDING_EVENT])
        shell.open_pull_requests = [{
            "number": 7, "url": PR_URL,
            "body": core.proposal_body(core.proposals([PENDING_EVENT])),
        }]

        propose_coverage.propose()

        assert shell.ran("gh", "pr", "create") == []
        assert shell.ran("gh", "pr", "edit")   == []

    def test_a_second_league_updates_the_open_one(self, shell):
        given(shell, [PENDING_EVENT, SECOND_EVENT])
        shell.open_pull_requests = [{
            "number": 7, "url": PR_URL,
            "body": core.proposal_body(core.proposals([PENDING_EVENT])),
        }]

        propose_coverage.propose()

        edited = shell.ran("gh", "pr", "edit")

        assert edited and edited[0][3] == "7"
        assert shell.ran("gh", "pr", "create") == []

    def test_a_gh_that_cannot_answer_stops_rather_than_opening_a_second(
            self, shell):
        # Everything else in this project degrades. Not knowing whether a pull
        # request is already open is the one state that cannot be guessed from:
        # assuming none opens a second review of the same question.
        given(shell, [PENDING_EVENT])
        shell.failing = ["gh", "pr", "list"]

        with pytest.raises(SystemExit):
            propose_coverage.propose()

        assert shell.ran("gh", "pr", "create") == []

    def test_a_body_somebody_rewrote_is_proposed_again_rather_than_lost(
            self, shell):
        # No marker means no answer, and proposing again is the safe direction:
        # the alternative is a league that silently never gets reviewed.
        given(shell, [PENDING_EVENT])
        shell.open_pull_requests = [
            {"number": 7, "url": PR_URL, "body": "lgtm"}
        ]

        propose_coverage.propose()

        assert shell.ran("gh", "pr", "edit")


class TestTheLinkOnTheDashboard:
    def test_the_url_is_recorded_against_the_event(self, shell):
        given(shell, [PENDING_EVENT])
        propose_coverage.propose()

        assert shell.resolution["events"][0]["pull_request"] == PR_URL

    def test_the_answers_generated_at_is_not_moved(self, shell):
        # It records when the *resolution* was reached, not when a link was
        # added to it — see `opendota_pipeline.write_resolution`.
        given(shell, [PENDING_EVENT])
        propose_coverage.propose()

        assert shell.resolution["generated_at"] == "2026-08-14T09:00:59+00:00"

    def test_it_is_committed_then_rebased_then_pushed(self, shell):
        # The master push, which is everything after the run leaves the
        # proposal branch. Commit first, because `git pull --rebase` refuses a
        # dirty tree; `--autostash`, because it refuses an unstaged tracked
        # file even before it fetches.
        given(shell, [PENDING_EVENT])
        propose_coverage.propose()

        order = [" ".join(cmd) for cmd in shell.commands]
        tail  = order[order.index("git checkout master") + 1:]
        verbs = [cmd.split()[1] for cmd in tail if cmd.startswith("git ")]

        assert verbs.index("commit") < verbs.index("pull") < verbs.index("push")
        assert "--autostash" in shell.ran("git", "pull")[0]

    def test_an_answer_with_no_url_in_it_records_nothing(self, shell):
        # `gh` prints notices of its own, and this string is written into a
        # committed file and rendered as a link. The pull request is open
        # either way — the next run reads its URL back from `gh pr list`.
        given(shell, [PENDING_EVENT])
        shell.created = "! Warning: 2 uncommitted changes\n"

        propose_coverage.propose()

        assert shell.ran("git", "add", "data/tier1_resolution.json") == []

    def test_recording_the_same_link_again_pushes_nothing(self, shell):
        given(shell, [dict(PENDING_EVENT, pull_request=PR_URL)])
        shell.open_pull_requests = [{
            "number": 7, "url": PR_URL,
            "body": core.proposal_body(core.proposals([PENDING_EVENT])),
        }]

        propose_coverage.propose()

        assert shell.ran("git", "commit") == []


# ── Closing it ────────────────────────────────────────────────────────────────


class TestRejection:
    def test_closing_records_the_refusal(self, shell):
        given(shell, [PENDING_EVENT])

        propose_coverage.reject(core.proposal_marker([19719]))

        verdicts = {entry["league_id"]: entry["verdict"]
                    for entry in shell.ledger["leagues"]}

        assert verdicts[19719] == core.LEDGER_REJECTED

    def test_the_refusal_is_pushed_to_master(self, shell):
        given(shell, [PENDING_EVENT])

        propose_coverage.reject(core.proposal_marker([19719]))
        verbs = [cmd[1] for cmd in shell.commands if cmd[0] == "git"]

        assert verbs.index("commit") < verbs.index("pull") < verbs.index("push")

    def test_a_body_naming_nothing_writes_nothing(self, shell):
        given(shell, [PENDING_EVENT])

        assert propose_coverage.reject("closing this, wrong tournament") == 0
        assert shell.commands == []
        assert shell.ledger == LEDGER

    def test_the_workflow_watches_the_branch_this_code_pushes(self):
        # The one name that has to be written twice — a YAML `if:` cannot read
        # a Python constant. Drift here is silent: every proposal would close
        # with nothing recorded, and every one of them would be proposed again
        # the next morning.
        closed = (WORKFLOWS / "proposal-closed.yml").read_text(encoding="utf-8")

        assert f"'{core.PROPOSAL_BRANCH}'" in closed

    def test_a_league_covered_in_the_meantime_is_left_covered(self, shell):
        # Proposed on Monday, activated by hand on Tuesday, pull request closed
        # on Wednesday. The close must not un-cover it.
        given(shell, [PENDING_EVENT])

        propose_coverage.reject(core.proposal_marker([17119]))

        assert shell.commands == []
        assert shell.ledger == LEDGER
