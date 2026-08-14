"""
What the scheduled update pushes, and what it refuses to.

Since issue 13 this runs on a GitHub Actions runner as well as on Wade's
workstation, and the two differ in one way that matters: a runner's checkout is
clean, and a workstation's may not be. `git add dashboard.py` on a dirty tree
deploys whatever half-finished dashboard edit happened to be sitting there —
which is the acceptance criterion "CI commits only its own outputs, never
`dashboard.py` wholesale".

Nothing here runs git. `auto_update.run` is the seam: every command the script
would have run is recorded instead, so the order of `pull` and `push` is
asserted on rather than performed.
"""
import pytest

import auto_update
import core

DASHBOARD = """# data: 2026-08-01
import streamlit as st

st.title("Dota 2")
"""


# ── The marker itself ─────────────────────────────────────────────────────────


class TestBumpingTheDataDate:
    def test_the_date_is_replaced(self):
        assert core.bump_data_date(DASHBOARD, "2026-08-14").startswith(
            "# data: 2026-08-14"
        )

    def test_nothing_else_moves(self):
        bumped = core.bump_data_date(DASHBOARD, "2026-08-14")

        assert bumped.splitlines()[1:] == DASHBOARD.splitlines()[1:]

    def test_a_file_with_no_marker_comes_back_unchanged(self):
        # The caller has to notice: without the marker, a data-only push may
        # not redeploy at all, and `ttl` on load_data() is not a substitute.
        assert core.bump_data_date("import streamlit\n", "2026-08-14") == \
            "import streamlit\n"


class TestWhetherAnybodyElseEditedTheDashboard:
    def test_an_untouched_file_matches_itself(self):
        assert core.same_but_for_the_data_date(DASHBOARD, DASHBOARD) is True

    def test_a_file_differing_only_in_the_marker_matches(self):
        # The stale-bump case: a previous run wrote the date and then failed
        # before committing. Refusing that forever would wedge every run after
        # it, which is worse than the thing being guarded against.
        stale = core.bump_data_date(DASHBOARD, "2026-08-13")

        assert core.same_but_for_the_data_date(DASHBOARD, stale) is True

    def test_a_real_edit_does_not(self):
        edited = DASHBOARD.replace('st.title("Dota 2")', 'st.title("WIP")')

        assert core.same_but_for_the_data_date(DASHBOARD, edited) is False

    def test_line_endings_do_not_decide_it(self):
        # `core.autocrlf` is on: the blob is stored with LF and checked out
        # with CRLF, so a raw comparison would answer "edited" for every file
        # in the repository — a guard that is off.
        assert core.same_but_for_the_data_date(
            DASHBOARD, DASHBOARD.replace("\n", "\r\n")
        ) is True


# ── The script around it ──────────────────────────────────────────────────────


@pytest.fixture
def git(tmp_path, monkeypatch):
    """
    Replaces `auto_update.run` and points the script's paths at `tmp_path`.

    Assign `git.committed` to what `git show HEAD:dashboard.py` should answer,
    and `git.status` to what `git status --porcelain` should. Every command
    lands on `git.commands`.
    """
    class Result:
        def __init__(self, stdout=""):
            self.stdout     = stdout
            self.stderr     = ""
            self.returncode = 0

    class Stub:
        def __init__(self):
            self.commands  = []
            self.committed = DASHBOARD
            self.status    = ""

        def run(self, cmd, check=True):
            self.commands.append([str(part) for part in cmd])

            if cmd[:2] == ["git", "show"]:
                return Result(self.committed)
            if cmd[:2] == ["git", "status"]:
                return Result(self.status)

            return Result()

    stub = Stub()
    dashboard = tmp_path / "dashboard.py"
    dashboard.write_text(DASHBOARD, encoding="utf-8")

    monkeypatch.setattr(auto_update, "run", stub.run)
    monkeypatch.setattr(auto_update, "DASHBOARD", dashboard)
    monkeypatch.setattr(auto_update, "LOG_FILE", tmp_path / "update_log.txt")
    monkeypatch.setattr(auto_update, "HERE", tmp_path)

    stub.dashboard = dashboard
    return stub


class TestBumpingTheDashboardOnDisk:
    def test_a_clean_tree_is_bumped_and_staged(self, git):
        assert auto_update.bump_dashboard("2026-08-14") is True
        assert git.dashboard.read_text(encoding="utf-8").startswith(
            "# data: 2026-08-14"
        )

    def test_a_local_edit_is_neither_bumped_nor_staged(self, git):
        git.dashboard.write_text(
            DASHBOARD.replace('st.title("Dota 2")', 'st.title("WIP")'),
            encoding="utf-8",
        )

        assert auto_update.bump_dashboard("2026-08-14") is False
        # Untouched: the run pushes data and leaves the source code alone.
        assert 'st.title("WIP")' in git.dashboard.read_text(encoding="utf-8")
        assert "# data: 2026-08-01" in git.dashboard.read_text(encoding="utf-8")

    def test_a_local_edit_is_reported(self, git):
        git.dashboard.write_text(DASHBOARD + "st.write('wip')\n",
                                 encoding="utf-8")

        auto_update.bump_dashboard("2026-08-14")

        assert "uncommitted changes" in (auto_update.LOG_FILE).read_text(
            encoding="utf-8"
        )

    def test_a_stale_bump_is_not_mistaken_for_an_edit(self, git):
        git.dashboard.write_text(core.bump_data_date(DASHBOARD, "2026-08-13"),
                                 encoding="utf-8")

        assert auto_update.bump_dashboard("2026-08-14") is True

    def test_a_dashboard_with_no_committed_version_is_bumped(self, git):
        # A first push, or a rename. `git show` fails and answers nothing;
        # there is no local edit to protect, because there is no baseline.
        git.committed = ""

        assert auto_update.bump_dashboard("2026-08-14") is True


class TestPushing:
    def test_the_remote_is_pulled_before_it_is_pushed_to(self, git):
        # Two writers now exist — the runner and the workstation — so a remote
        # ahead of local must rebase rather than fail the run.
        auto_update.commit_and_push(["data/matches_flat.csv"], "2026-08-14")

        verbs = [cmd[1] for cmd in git.commands if cmd[0] == "git"]

        assert verbs.index("pull") < verbs.index("push")

    def test_the_commit_happens_before_the_pull(self, git):
        # `git pull --rebase` refuses a dirty tree, so the run's own work has
        # to be a commit before the remote's can be rebased under it.
        auto_update.commit_and_push(["data/matches_flat.csv"], "2026-08-14")
        verbs = [cmd[1] for cmd in git.commands if cmd[0] == "git"]

        assert verbs.index("commit") < verbs.index("pull")

    def test_only_the_named_paths_are_staged(self, git):
        auto_update.commit_and_push(["data/matches_flat.csv"], "2026-08-14")

        staged = next(cmd for cmd in git.commands if cmd[:2] == ["git", "add"])

        assert staged == ["git", "add", "data/matches_flat.csv"]
