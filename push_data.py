"""
Run this after the pipeline to push updated data to prod, by hand.

Bumps the date comment in dashboard.py so Streamlit Cloud always redeploys, and
rebases on the remote first — since issue 13 the runner pushes too, so a remote
ahead of this workstation is ordinary rather than a fault.

Unlike `auto_update.py` this one bumps and stages `dashboard.py` unconditionally:
it is run by hand, immediately after the pipeline, by the person whose edits
those would be. `auto_update.py` runs unattended and does not get to assume that.
"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import core

HERE = Path(__file__).parent
DASHBOARD = HERE / "dashboard.py"
DATE = datetime.now().strftime("%Y-%m-%d")


def run(cmd):
    result = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"FAILED: {' '.join(cmd)}\n{result.stderr}")
        sys.exit(1)
    return result


# Bump the date comment in dashboard.py
DASHBOARD.write_text(
    core.bump_data_date(DASHBOARD.read_text(encoding="utf-8"), DATE),
    encoding="utf-8",
)
print(f"Bumped dashboard.py date to {DATE}")

# Commit and push.
# Only stage what exists: `git add` exits 128 on a pathspec matching no file,
# which would abort the push. build_dataframe() writes neither the CSV nor
# meta.json when there is no raw store to flatten.
DEPLOYED = [
    "data/matches_flat.csv",
    "data/meta.json",
    # Changes only on `python liquipedia.py --refresh-seed`, but it has to ship
    # when it does: the Upcoming tab reads it from the deployed app, and a
    # refresh left unstaged would sit dirty in the working tree indefinitely.
    "data/tier1_calendar.json",
    # The league ledger. The pipeline appends leagues it has newly discovered,
    # and that is news — a pending entry left unstaged is a candidate tournament
    # nobody ever sees, which is the failure the ledger exists to prevent.
    "data/leagues.json",
    # Which Tier 1 event resolved to which league, and which events resolved to
    # nothing. Rewritten only when that answer changes, so it is never a commit
    # carrying a fresh timestamp and no news.
    "data/tier1_resolution.json",
    # The Standard modelling store. Nothing deployed reads it — it ships because
    # a modelling asset that lives on one workstation is the thing the artifact
    # split was for. Appended to, never rewritten, so a commit carries only the
    # matches the run added.
    "data/matches_standard.jsonl",
    "dashboard.py",
]
present = [p for p in DEPLOYED if (HERE / p).exists()]
run(["git", "add", *present])
run(["git", "commit", "-m", f"data: update matches ({DATE})"])
# Commit, then pull, then push: `git pull --rebase` refuses a dirty tree.
run(["git", "pull", "--rebase", "origin", "master"])
run(["git", "push", "origin", "master"])
print("Pushed — Streamlit Cloud will redeploy automatically.")
