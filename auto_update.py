"""
Scheduled update: runs the pipeline, then pushes if anything changed.

Since issue 13 this runs on a GitHub Actions runner every six hours as well as
by hand on the workstation, and nothing here needs to know which. The one
difference that matters is that a runner's checkout is clean and a workstation's
may not be — which is what `bump_dashboard` is about.

The module does nothing when imported: `main()` is the only entry point, and
`run()` is the seam the tests replace so that not one git command is executed.
"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import core

HERE = Path(__file__).parent
DASHBOARD = HERE / "dashboard.py"
LOG_FILE = HERE / "update_log.txt"
PYTHON = sys.executable

# The files a run may push. Every one of them is written by the pipeline, except
# `dashboard.py`, which carries only the redeploy marker — and only when nobody
# else has edited it. `push_data.py` holds the same list, and adding a deployed
# data file means editing both.
DEPLOYED = [
    "data/matches_flat.csv",
    "data/meta.json",
    "data/tier1_calendar.json",
    "data/leagues.json",
    "data/tier1_resolution.json",
    "data/matches_standard.jsonl",
]

# What counts as news, and therefore as a reason to push. The ledger counts
# alongside the CSV: a run that fetched no matches but discovered a new league
# has found the one thing this job exists to notice, and testing the CSV alone
# would leave that pending entry dirty in the working tree until somebody
# happened to look. The Tier 1 resolution counts for the same reason and is safe
# to test on, because it is rewritten only when the mapping itself changes.
NEWSWORTHY = [
    "data/matches_flat.csv",
    "data/leagues.json",
    "data/tier1_resolution.json",
]


def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(cmd, check=True):
    result = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True)
    if check and result.returncode != 0:
        log(f"FAILED: {' '.join(str(c) for c in cmd)}\n{result.stderr}")
        sys.exit(1)
    return result


def run_pipeline():
    """
    Runs the pipeline and logs the run's own summary line.

    The line, not the last line of stdout. The last line of a successful run is
    whatever happened to print last — a column name, for two months (ADR-0004).
    The summary is printed at the end of `main()`, so its absence here means the
    run did not get that far, which is a different entry from a run that
    finished having fetched nothing. See ADR-0011.
    """
    log("Running pipeline...")

    result  = run([PYTHON, str(HERE / "opendota_pipeline.py")])
    summary = core.read_run_summary(result.stdout)

    if summary:
        log(f"Pipeline: {summary}")
        return

    tail = result.stdout.strip().splitlines()
    log("Pipeline: FINISHED WITHOUT A SUMMARY — the run did not reach the end")
    log(f"Pipeline: last output was {tail[-1].strip() if tail else '(no output)'}")


def what_changed():
    """
    The porcelain status of the files that count as news, or "" for none.

    `status --porcelain`, not `diff --stat`: a file the pipeline created rather
    than edited is untracked, and `git diff` says nothing at all about an
    untracked path. A ledger regenerated from scratch would have reported
    "nothing to push" — and so would a first CSV.
    """
    return run(["git", "status", "--porcelain", "--", *NEWSWORTHY],
               check=False).stdout.strip()


def bump_dashboard(day):
    """
    Moves the `# data:` comment in `dashboard.py` to `day` and says whether the
    file may be pushed.

    Streamlit Cloud does not reliably redeploy on a push that changes only data
    files, so the marker is what makes the new CSV visible. The `ttl` on
    `load_data()` is not a substitute: an expiring cache re-reads the same stale
    file inside the same container.

    **It refuses when somebody else has edited the file.** On a runner that
    never happens; on the workstation `git add dashboard.py` would sweep up
    whatever half-finished dashboard edit was sitting in the tree and deploy it
    to a public app. A run that refuses still pushes its data — it costs a
    redeploy that may not fire until the next push, which is the cheaper of the
    two failures by a wide margin.

    A file differing from the committed one in *nothing but* the marker is not
    an edit: that is a previous run that bumped and then failed, and refusing it
    would wedge every run after it.
    """
    committed = run(["git", "show", "HEAD:dashboard.py"], check=False).stdout
    working   = DASHBOARD.read_text(encoding="utf-8")

    # No committed version is a first push or a rename, not an edit — there is
    # no baseline, so there is nothing to protect.
    if committed and not core.same_but_for_the_data_date(committed, working):
        log("dashboard.py has uncommitted changes — not bumping or staging it. "
            "The data is still being pushed; Streamlit may not redeploy until "
            "dashboard.py is pushed by hand.")
        return False

    bumped = core.bump_data_date(working, day)

    if bumped == working and f"# data: {day}" not in working:
        log("dashboard.py carries no '# data:' comment — nothing to bump, and "
            "Streamlit may not redeploy on a data-only push.")
        return False

    DASHBOARD.write_text(bumped, encoding="utf-8")
    log(f"Bumped dashboard.py date to {day}")
    return True


def commit_and_push(paths, day):
    """
    Commits `paths` and pushes them, rebasing on the remote first.

    The pull is not optional any more. Until issue 13 there was one writer;
    there are now two — the runner every six hours, and the workstation by hand
    — so a remote ahead of local is an ordinary Tuesday rather than a fault. The
    order is commit, then pull, then push, because `git pull --rebase` refuses a
    dirty tree and the run's own work has to be a commit before the remote's can
    be rebased under it.

    A conflict here fails the run, which is right: the files that could conflict
    are the data files, and the resolution for those is to run the pipeline
    again — which is what the next scheduled run does.
    """
    run(["git", "add", *paths])
    run(["git", "commit", "-m", f"data: auto-update matches ({day})"])
    run(["git", "pull", "--rebase", "origin", "master"])
    run(["git", "push", "origin", "master"])


def main():
    log("=== Auto update started ===")

    run_pipeline()

    changed = what_changed()

    if not changed:
        log("No new matches and no new leagues — nothing to push.")
        log("=== Done ===\n")
        return

    log(f"Changed: {changed}")

    day = datetime.now().strftime("%Y-%m-%d")

    # Only stage what exists — `git add` exits 128 on a pathspec matching no
    # file, which would abort the run with the tree already dirtied.
    paths = [path for path in DEPLOYED if (HERE / path).exists()]

    if bump_dashboard(day):
        paths.append("dashboard.py")

    commit_and_push(paths, day)

    log("Pushed — Streamlit Cloud will redeploy automatically.")
    log("=== Done ===\n")


if __name__ == "__main__":
    main()
