"""
Scheduled daily update: runs the pipeline then pushes if new data was fetched.
"""
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
DASHBOARD = HERE / "dashboard.py"
LOG_FILE = HERE / "update_log.txt"
PYTHON = sys.executable
DATE = datetime.now().strftime("%Y-%m-%d")


def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def run(cmd, check=True):
    result = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True)
    if check and result.returncode != 0:
        log(f"FAILED: {' '.join(str(c) for c in cmd)}\n{result.stderr}")
        sys.exit(1)
    return result


log("=== Auto update started ===")

# Step 1 — run the pipeline
log("Running pipeline...")
result = run([PYTHON, str(HERE / "opendota_pipeline.py")])
for line in reversed(result.stdout.strip().splitlines()):
    if line.strip():
        log(f"Pipeline: {line.strip()}")
        break

# Step 2 — check whether anything actually changed
# The ledger counts alongside the CSV: a run that fetched no matches but
# discovered a new league has found the one thing this job exists to notice, and
# exiting on the CSV alone would leave that pending entry dirty in the working
# tree until someone happened to look. The Tier 1 resolution counts for the same
# reason and is safe to test, because it is only rewritten when the mapping
# itself changes — a new tournament recognised, or a gap closed.
#
# `status --porcelain`, not `diff --stat`: a file the pipeline created rather
# than edited is untracked, and `git diff` says nothing at all about an
# untracked path. A ledger regenerated from scratch would have reported "nothing
# to push" — and so would a first CSV.
diff = run(["git", "status", "--porcelain", "--",
            "data/matches_flat.csv", "data/leagues.json",
            "data/tier1_resolution.json"], check=False)
if not diff.stdout.strip():
    log("No new matches and no new leagues — nothing to push.")
    log("=== Done ===\n")
    sys.exit(0)

log(f"Changed: {diff.stdout.strip()}")

# Step 3 — bump date in dashboard.py so Streamlit always redeploys
text = DASHBOARD.read_text(encoding="utf-8")
text = re.sub(r"^# data: \d{4}-\d{2}-\d{2}", f"# data: {DATE}", text, flags=re.MULTILINE)
DASHBOARD.write_text(text, encoding="utf-8")
log(f"Bumped dashboard.py date to {DATE}")

# Step 4 — commit and push
# Only stage what exists — `git add` exits 128 on a pathspec matching no file,
# which would abort the run after Step 3 has already dirtied dashboard.py.
DEPLOYED = [
    "data/matches_flat.csv",
    "data/meta.json",
    # See push_data.py — the two lists must stay in step.
    "data/tier1_calendar.json",
    "data/leagues.json",
    "data/tier1_resolution.json",
    "data/matches_standard.jsonl",
    "dashboard.py",
]
run(["git", "add", *[p for p in DEPLOYED if (HERE / p).exists()]])
run(["git", "commit", "-m", f"data: auto-update matches ({DATE})"])
run(["git", "push", "origin", "master"])

log("Pushed — Streamlit Cloud will redeploy automatically.")
log("=== Done ===\n")
