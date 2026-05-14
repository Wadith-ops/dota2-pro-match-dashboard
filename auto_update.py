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

# Step 2 — check if CSV actually changed
diff = run(["git", "diff", "--stat", "data/matches_flat.csv"], check=False)
if not diff.stdout.strip():
    log("No new matches — nothing to push.")
    log("=== Done ===\n")
    sys.exit(0)

log(f"CSV changed: {diff.stdout.strip()}")

# Step 3 — bump date in dashboard.py so Streamlit always redeploys
text = DASHBOARD.read_text(encoding="utf-8")
text = re.sub(r"^# data: \d{4}-\d{2}-\d{2}", f"# data: {DATE}", text, flags=re.MULTILINE)
DASHBOARD.write_text(text, encoding="utf-8")
log(f"Bumped dashboard.py date to {DATE}")

# Step 4 — commit and push
run(["git", "add", "data/matches_flat.csv", "dashboard.py"])
run(["git", "commit", "-m", f"data: auto-update matches ({DATE})"])
run(["git", "push", "origin", "master"])

log("Pushed — Streamlit Cloud will redeploy automatically.")
log("=== Done ===\n")
