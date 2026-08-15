"""
Proposes a coverage decision as a pull request, and records the answer.

The last mile of the resolver (`tier1-pipeline-automation/15`). Everything up to
here finds which OpenDota league a Liquipedia Tier 1 event was played in; none
of it may decide to cover one. That decision is Wade's, and this is where it is
put to him: a pull request changing one league's verdict to `active` in
`data/leagues.json`.

**Merging is approval. Closing is rejection.** A merge puts the `active` verdict
on `master`, and the next scheduled run fetches the league with no further step.
A close fires `.github/workflows/proposal-closed.yml`, which runs this script
again with `--reject` and records `rejected`, so the league is never proposed
again — reversibly, like every verdict in that file.

Two things keep the two paths apart from the data push:

- **The proposal lives on its own branch.** `auto_update.py` pushes data
  straight to `master` and must stay that way; nothing here touches `master`
  except to record the pull request's URL on `data/tier1_resolution.json`, which
  is what lights up the Review link on the dashboard's Upcoming tab.
- **One branch, therefore one open pull request.** A later run that finds a
  second league updates the open one rather than opening a second review of the
  same question, and a run that finds nothing new does nothing at all.

The module does nothing when imported: `main()` is the only entry point, and
`run()` is the seam `tests/test_proposal_shell.py` replaces so that not one git
or `gh` command is executed by the suite.

```bash
python propose_coverage.py            # propose, if anything is awaiting a verdict
python propose_coverage.py --dry-run  # print the pull request, write nothing
python propose_coverage.py --reject   # record a rejection; the body is $PR_BODY
```
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import core
from opendota_pipeline import write_text_atomic

HERE = Path(__file__).parent

# Relative, because these strings are also what `git add` is given, and git
# wants a path inside the repository rather than an absolute one.
LEDGER_PATH     = "data/leagues.json"
RESOLUTION_PATH = "data/tier1_resolution.json"

LEDGER_FILE     = HERE / LEDGER_PATH
RESOLUTION_FILE = HERE / RESOLUTION_PATH

# The branch every proposal is built on, and the branch it targets. One branch
# is what makes "only one open pull request at a time" a property of the design
# rather than something to check for.
BRANCH = core.PROPOSAL_BRANCH
BASE   = "master"


def run(cmd, check=True):
    """
    Runs a command and returns the result. **UTF-8, explicitly.**

    The same rule `auto_update.run` follows and for the same reason: `text=True`
    alone decodes with the locale's preferred encoding — cp1252 on Wade's
    workstation — and every file in this repository is UTF-8. A tournament name
    with an em dash in it would otherwise take the run down inside subprocess's
    reader thread rather than anywhere it could be caught.
    """
    result = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")

    if check and result.returncode != 0:
        print(f"FAILED: {' '.join(str(part) for part in cmd)}\n{result.stderr}",
              flush=True)
        sys.exit(1)

    return result


def read_json(path):
    """A committed side file, or `{}` when it is absent or damaged."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            document = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

    return document if isinstance(document, dict) else {}


def write_ledger(ledger):
    """The ledger, one league per line — `core.format_ledger`'s shape."""
    write_text_atomic(str(LEDGER_FILE), core.format_ledger(ledger))


def write_resolution(report):
    """The resolution report, in the shape the pipeline writes it."""
    write_text_atomic(
        str(RESOLUTION_FILE),
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
    )


def commit_to_base(paths, message):
    """
    Commits `paths` on the current branch and pushes them to `master`.

    Commit, then pull, then push, and the pull carries `--autostash` — the same
    order and the same reasons as `auto_update.commit_and_push`. There are three
    writers of `master` now rather than two, and this one runs in the same job
    as the six-hourly data push.
    """
    run(["git", "add", *paths])
    run(["git", "commit", "-m", message])
    run(["git", "pull", "--rebase", "--autostash", "origin", BASE])
    run(["git", "push", "origin", BASE])


# ── The pull request ──────────────────────────────────────────────────────────


def open_proposal():
    """
    The open pull request on the proposal branch, or None when there is none.

    A failure here **ends the run**. Every other fetch in this project degrades,
    but not knowing whether a pull request is already open is the one state that
    cannot be guessed from: assuming none opens a second review of the same
    question, which is the acceptance criterion this branch exists to keep.
    """
    listed = json.loads(run([
        "gh", "pr", "list", "--head", BRANCH, "--state", "open",
        "--json", "number,body,url",
    ]).stdout or "[]")

    return listed[0] if listed else None


def push_branch(league_ids, message):
    """
    Rebuilds the proposal branch from the current commit with those leagues
    marked `active`, and force-pushes it.

    Rebuilt rather than added to, because the branch is a proposal and not a
    history: what it must show a reviewer is one commit against today's
    `master`, whatever it showed yesterday. The cost is that a hand edit made on
    the branch — correcting a league's name before merging — is lost if a second
    league joins the proposal before it is merged. That is the rarer of the two,
    and the alternative is a review of a diff nobody can read.

    `--force`, not `--force-with-lease`: the branch is built with `checkout -B`
    from `master`, so this checkout has no tracking ref for it to lease against
    and the lease would fail every time it mattered.

    The branch is left as it was found — the caller's next move is on `master`,
    and a job left on a side branch pushes the wrong thing.
    """
    # This expects to be on `master`: that is what the data job checks out, and
    # what a proposal is a diff against. A detached HEAD — `rev-parse` answers
    # "HEAD" — or a run started on the proposal branch itself would strand the
    # commit that records the pull request's URL afterwards, since that one is
    # pushed to `master` by name. Both are coerced rather than trusted.
    started = run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()

    if started in ("HEAD", BRANCH, ""):
        started = BASE

    ledger = read_json(LEDGER_FILE)

    if not core.ledger_entries(ledger):
        # Missing or damaged. Every verdict in that file survives a syntax
        # error and none of it survives being written over — the same rule
        # `load_ledger` follows, and the reason nothing is written here either.
        print("  The ledger could not be read — nothing proposed")
        return False

    ledger, changes = core.record_verdicts(
        ledger, league_ids, core.LEDGER_ACTIVE
    )

    if not changes:
        # Every league in the queue already holds a verdict on disk. The
        # resolution file is one run staler than the ledger, which is ordinary
        # right after a merge.
        print("  The ledger already answers all of these — no branch pushed")
        return False

    run(["git", "checkout", "-B", BRANCH])
    write_ledger(ledger)
    run(["git", "add", LEDGER_PATH])
    run(["git", "commit", "-m", message])
    run(["git", "push", "--force", "origin", BRANCH])
    run(["git", "checkout", started])

    for league_id, before, after in changes:
        print(f"  League {league_id} proposed: {before} -> {after}")

    return True


def create_or_update(existing, title, body):
    """
    Opens the pull request, or brings the open one up to date. Returns its URL.

    The body goes through a file rather than an argument: it is a page of
    markdown, and a command line is neither the place for it nor reliably long
    enough for one.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".md", encoding="utf-8",
                                     delete=False) as f:
        f.write(body)
        body_file = f.name

    try:
        if existing:
            run(["gh", "pr", "edit", str(existing["number"]),
                 "--title", title, "--body-file", body_file])
            print(f"  Updated {existing['url']}")
            return existing["url"]

        created = run(["gh", "pr", "create", "--base", BASE, "--head", BRANCH,
                       "--title", title, "--body-file", body_file]).stdout

        # The URL, not the whole of stdout: `gh` prints notices of its own
        # around it, and this string is written into a committed file and
        # rendered as a link on the dashboard. An answer with no URL in it
        # records nothing — the pull request is open either way, and the next
        # run reads its URL back from `gh pr list`.
        url = next((line.strip() for line in reversed(created.splitlines())
                    if line.strip().startswith("http")), "")

        print(f"  Opened {url or '(a pull request gh did not name)'}")
        return url
    finally:
        os.unlink(body_file)


# ── The two entry points ──────────────────────────────────────────────────────


def propose(dry_run=False, today=None):
    """
    Opens or updates the one pull request proposing the leagues awaiting a
    verdict. Does nothing, loudly or otherwise, when there are none.
    """
    report  = read_json(RESOLUTION_FILE)
    records = report.get("events") or []
    queue   = core.proposals(records)

    if not queue:
        print("Nothing is awaiting a verdict — no pull request")
        return 0

    # Only the overdue ones. A tournament that has not started yet is a gap
    # every run, and repeating it in every proposal is how a real one stops
    # being read — the dashboard's Upcoming tab is where the full list lives.
    today = today or datetime.now(timezone.utc).date()
    gaps  = [gap for gap in core.coverage_gaps(records, today)
             if gap["state"] == core.GAP_OVERDUE]

    proposed = sorted(proposal["league_id"] for proposal in queue)
    title    = core.proposal_title(queue)
    body     = core.proposal_body(queue, gaps)

    print("Awaiting a verdict: "
          + ", ".join(f"{proposal['event']} (league {proposal['league_id']})"
                      for proposal in queue))

    if dry_run:
        print("\n" + title + "\n\n" + body)
        return 0

    existing = open_proposal()

    if existing and core.proposed_league_ids(existing["body"]) == proposed:
        print(f"  Already proposed in {existing['url']}")
        url = existing["url"]
    else:
        # The title with its first letter lowered, rather than `.lower()`: the
        # rest of it is a tournament's name, and this project has learned twice
        # over what re-casing one costs.
        message = "coverage: " + title[0].lower() + title[1:]

        if not push_branch(proposed, message):
            return 0
        url = create_or_update(existing, title, body)

    # The link the dashboard's Upcoming tab renders. Recorded on `master`, and
    # only when it moves: this file is committed and pushed, and a run
    # re-deriving the same answer must not put a commit in front of Wade every
    # six hours saying nothing but the time of day.
    updated, changed = core.record_pull_request(records, url, proposed)

    if url and changed:
        write_resolution(dict(report, events=updated))
        commit_to_base([RESOLUTION_PATH],
                       f"coverage: link the pull request awaiting review ({url})")

    return 0


def reject(body):
    """
    Records a `rejected` verdict for every league the closed pull request
    proposed, so that none of them is ever proposed again.

    The leagues come from the marker in the body, which is the only thing GitHub
    hands the closing workflow that this project wrote itself. A body somebody
    rewrote by hand names nothing and nothing is recorded — the league stays
    pending and is proposed again on the next run, which is the safe direction.

    A verdict already on record is left alone by `core.record_verdicts`, so
    closing a stale proposal cannot un-cover a league covered by hand in the
    meantime.
    """
    league_ids = core.proposed_league_ids(body)

    if not league_ids:
        print("The pull request body named no leagues — nothing recorded")
        return 0

    ledger, changes = core.record_verdicts(
        read_json(LEDGER_FILE), league_ids, core.LEDGER_REJECTED
    )

    if not changes:
        print(f"Nothing to record: {league_ids} already hold a verdict")
        return 0

    write_ledger(ledger)

    for league_id, before, after in changes:
        print(f"League {league_id} refused: {before} -> {after}")

    commit_to_base(
        [LEDGER_PATH],
        "coverage: record the refusal of "
        + ", ".join(f"league {league_id}" for league_id, _, _ in changes),
    )

    return 0


def main(argv=None):
    # Python picks the *locale's* encoding for a stdout that is not a console —
    # cp1252 on Wade's workstation — so a redirected run dies on the first
    # non-ASCII character it prints. `--dry-run` prints a pull request body full
    # of em dashes, and the runner tees this job's output through a pipe. Not a
    # cosmetic failure: it raises, and it raises *after* the branch is pushed.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument(
        "--reject", action="store_true",
        help="record a rejection; the closed pull request's body is $PR_BODY",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print the pull request that would be opened, and write nothing",
    )
    args = parser.parse_args(argv)

    if args.reject:
        # From the environment rather than an argument: the body is somebody
        # else's text, a page long, and a command line is where that becomes a
        # quoting problem with teeth.
        return reject(os.getenv("PR_BODY", ""))

    return propose(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
