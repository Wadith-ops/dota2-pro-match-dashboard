"""
Run this after the pipeline to push updated data to prod.
Bumps the date comment in dashboard.py so Streamlit Cloud always redeploys.
"""
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

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
text = DASHBOARD.read_text(encoding="utf-8")
text = re.sub(r"^# data: \d{4}-\d{2}-\d{2}", f"# data: {DATE}", text, flags=re.MULTILINE)
DASHBOARD.write_text(text, encoding="utf-8")
print(f"Bumped dashboard.py date to {DATE}")

# Commit and push
run(["git", "add", "data/matches_flat.csv", "dashboard.py"])
run(["git", "commit", "-m", f"data: update matches ({DATE})"])
result = run(["git", "push", "origin", "master"])
print("Pushed — Streamlit Cloud will redeploy automatically.")
