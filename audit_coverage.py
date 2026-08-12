"""
Audits the dataset's coverage against Liquipedia's Tier 1 list, in both
directions, over a period given on the command line.

The daily pipeline resolves *forward*: it looks only at Tier 1 events that have
started, are not already mapped, and opened within the year. That is the right
shape for a job that runs every morning and the wrong shape for the question
this asks — five of the tracked leagues were chosen by hand before any of the
resolver existed, and nothing has ever re-derived them. So this re-resolves a
bounded period **in full**, mapped or not, and then checks the other way round:
a league being fetched that no Tier 1 event claims.

Run by hand, not on a schedule. It walks OpenDota's pro match list back to the
start of the period, which is minutes of API time, and it exists to be read
rather than to be automated.

    python audit_coverage.py                        # the dataset's whole span
    python audit_coverage.py --from 2025-10-01 --to 2025-12-31
    python audit_coverage.py --dry-run              # write nothing

**It never changes a verdict.** Recognising a tournament is the resolver's job
and covering one is Wade's, taken by editing `data/leagues.json` — so what this
writes is `tier1_event`, the same field the daily run writes, and what it prints
is the list of decisions waiting to be made. See `tier1-pipeline-automation/14`.
"""
import argparse
import sys
from datetime import date, datetime, timezone

import liquipedia
import opendota_pipeline as pipeline
from core import (
    apply_resolutions,
    audit_findings,
    audit_totals,
    audit_tracked_leagues,
    build_rows,
    candidates_in_window,
    dataset_start_date,
    events_in_range,
    format_audit_next_steps,
    format_audit_report,
    known_team_ids,
    league_catalogue,
    recordable_resolutions,
    resolution_problems,
    resolution_walk_start,
    resolve_tier1_events,
    summarise_leagues,
)

# The page cap for the audit's own walk, which is a different walk from the
# daily one: it reaches back to the start of the dataset rather than to the
# oldest unresolved event of the last day or two. A page is a hundred matches,
# so this is 100,000 — and it costs nothing unless it is needed, because the
# walk stops the moment it crosses its cutoff either way.
AUDIT_MAX_PAGES = 1000


def load_ledger(api_leagues, dry_run):
    """
    The ledger to audit against.

    `--dry-run` reads the file and stops there. The pipeline's `load_ledger`
    does more than read: it records newly discovered leagues as `pending` and
    **rewrites the file** when it finds any, and seeds a fresh 10,050-line one
    when the file is missing. Both are right for a daily run and neither is
    something a command promising to write nothing may do — so a dry run gets
    the plain read, and reports rather than repairs a file it cannot parse.
    """
    if not dry_run:
        return pipeline.load_ledger(api_leagues)

    ledger = pipeline.read_ledger()

    if ledger is None:
        print(f"  Could not read {pipeline.LEDGER_FILE} — a dry run will not "
              f"seed or repair it")
        return {"leagues": []}

    print(f"  Ledger: {len(ledger.get('leagues') or [])} leagues, read only")

    return ledger


def known_teams():
    """
    Every team already in the dataset, which is what the resolver's tiebreaker
    is measured against.

    Read from the Standard store through the same flattening the CSV uses, so
    the audit ranks candidates against exactly the set the daily run does.

    The patch map is `{}` deliberately: these rows are read for their team ids
    and nothing else, and the patch label is the only field that map decides.
    Fetching the patch constants to build a column nobody here reads would be a
    call spent on nothing.
    """
    rows = build_rows(pipeline.read_standard_store(), {})

    if not rows:
        print("No Standard records — run the pipeline before auditing")

    return rows, known_team_ids(rows)


def audit_period(rows, opens, closes):
    """
    The period to audit, as a pair of ISO dates.

    Both ends default to the data rather than to a constant. The start is the
    dataset's own earliest match, because an event that finished before the
    dataset began is outside its intended range and not a coverage gap; the end
    is today, because an event that has not started has nothing to find and is
    already reported as an upcoming gap by the daily run.
    """
    opens  = opens or dataset_start_date(rows)
    closes = closes or date.today().isoformat()

    return opens, closes


def shortlist_candidates(events, pro_matches, catalogue):
    """
    The leagues worth re-reading in full: every one whose matches, as far as
    the walk saw them, fall inside one of the audited windows.

    The walk alone cannot decide anything. A league that began before the walk
    did looks narrower here than it is, and narrow is the direction that makes
    a league appear to fit inside an event it has no business winning — so
    `confirm_candidates` re-reads each of these before any of them can win.
    """
    unconfirmed = summarise_leagues(pro_matches, catalogue)

    return sorted({
        summary["league_id"]
        for event in events
        for summary in candidates_in_window(
            unconfirmed, event.get("start_date"), event.get("end_date")
        )
    })


def walk_for_candidates(events, catalogue, max_pages):
    """
    Walks OpenDota's pro match list back to the earliest audited window, then
    re-reads every league that fell inside one.

    Returns the confirmed candidate pool. How far the walk actually got is
    printed rather than returned, and it is not decoration: a walk cut short by
    the page cap or a refused request still has to say so, because the events it
    never reached resolve to nothing, and a gap that is really "the walk ran out
    of pages" must not read like a gap that is really "OpenDota has no data for
    this tournament".
    """
    since = resolution_walk_start(events)

    print(f"  Walking pro matches back to "
          f"{datetime.fromtimestamp(since, tz=timezone.utc):%Y-%m-%d}...")

    pro_matches = pipeline.fetch_pro_matches(since, max_pages=max_pages)

    if not pro_matches:
        print("  Read no pro matches — nothing can resolve this run")
        return {}

    # `is not None`, never `or 0` — the same rule `fetch_pro_matches` follows
    # one call up. A single row with no start time would make this the epoch,
    # print a walk reaching back to 1970 and, worse, make the "stopped short"
    # test below false: the warning that separates "we ran out of pages" from
    # "OpenDota has no data" would go missing exactly when it is needed.
    played = [match["start_time"] for match in pro_matches
              if match.get("start_time") is not None]

    if not played:
        print(f"  Read {len(pro_matches)} pro matches, none carrying a start "
              f"time — nothing can be placed in a window")
        return {}

    reached = min(played)

    print(f"  Read {len(pro_matches)} pro matches back to "
          f"{datetime.fromtimestamp(reached, tz=timezone.utc):%Y-%m-%d}")

    if reached > since:
        print("  The walk stopped short of its cutoff — events before "
              f"{datetime.fromtimestamp(reached, tz=timezone.utc):%Y-%m-%d} "
              f"cannot resolve, and will be reported as gaps they may not be")

    shortlist = shortlist_candidates(events, pro_matches, catalogue)
    print(f"  {len(shortlist)} league(s) fall inside an audited window — "
          f"re-reading each in full")

    return pipeline.confirm_candidates(shortlist, catalogue)


def record_resolutions(ledger, resolutions, findings, dry_run):
    """
    Writes what the audit worked out onto the ledger: `tier1_event`, and only
    that.

    `apply_resolutions` never touches a verdict, so the worst this can do is
    name the tournament a league played — which is discovery, and is what the
    daily run writes too. It does **not** follow that every resolution is safe
    to write: `core.recordable_resolutions` holds back the ones that would
    displace an existing answer rather than add one, and they are reported as
    withheld rather than dropped in silence.

    `data/tier1_resolution.json` is deliberately left alone. That file's horizon
    is this year and next; an audit of the back catalogue writing history into
    it would put events nobody can act on onto the dashboard's Upcoming tab.
    """
    resolutions, withheld = recordable_resolutions(resolutions, findings)

    for resolution in withheld:
        print(f"  Withheld: '{resolution['event']}' → league "
              f"{resolution['league_id']} is not written, because doing so "
              f"would displace a mapping rather than add one")

    ledger, changes = apply_resolutions(ledger, resolutions)

    if not changes:
        print("  The ledger already records every mapping this audit derived")
        return ledger

    for league_id, before, after in changes:
        print(f"  League {league_id}: {before or 'no event'} -> '{after}'")

    if dry_run:
        print(f"  {len(changes)} mapping(s) not written — this is a dry run")
        return ledger

    pipeline.write_ledger(ledger)
    print(f"  {len(changes)} mapping(s) written to {pipeline.LEDGER_FILE}")

    return ledger


def run_audit(opens=None, closes=None, dry_run=False, max_pages=AUDIT_MAX_PAGES):
    """
    The whole audit: the period, the walk, the ranking, the report, and the one
    field it is allowed to write.

    Returns the findings, so a caller — a test, or a later session recording a
    fixture — can read the answer rather than the printout.
    """
    pipeline.ensure_directories()

    print("=" * 60)
    print("Tier 1 coverage audit")
    print("=" * 60)

    api_leagues = pipeline.fetch_all_leagues()
    ledger      = load_ledger(api_leagues, dry_run)

    rows, teams   = known_teams()
    opens, closes = audit_period(rows, opens, closes)

    if opens is None:
        # No matches and no --from, so there is no period. Auditing from the
        # beginning of Liquipedia's calendar would walk twenty years of pro
        # matches to report that an empty dataset is missing everything.
        print("  No period to audit: the dataset is empty and no --from was "
              "given")
        return []

    if not teams:
        # The tiebreaker is measured against the teams already in the dataset,
        # so with none every candidate scores zero, the primary ranking key
        # collapses, and windows are decided by coverage and match count alone.
        # That answer would then be written to the ledger. Refuse instead.
        print("  No tracked teams to rank candidates against — run the "
              "pipeline before auditing")
        return []

    print(f"  Auditing {opens} to {closes}, against {len(teams)} tracked teams")

    calendar = liquipedia.get_tier1_events()
    events   = calendar["events"]
    print(f"  Tier 1 calendar: {len(events)} events "
          f"(source: {calendar['source']})")

    # Computed before the range guard, deliberately. The reverse check reads the
    # whole calendar and the whole ledger and depends on the audited period not
    # at all, so a period holding no events is no reason to skip it.
    tracked = audit_tracked_leagues(ledger, events)
    audited = events_in_range(events, opens, closes)

    if not audited:
        print(format_audit_report([], tracked, opens, closes))
        return []

    print(f"  {len(audited)} Tier 1 event(s) fall inside the period")

    # `not api_leagues`, never `is None` — the same rule the ledger's seed path
    # follows and the opposite of the one the match loop follows. A league's
    # *match* list is legitimately empty before it starts; the *league* list is
    # not, since OpenDota has ten thousand. An empty one would leave every
    # summary with no tier, which silently switches off the prune that keeps
    # showmatch series out of a Tier 1 window — and the resulting mappings would
    # then be written to the ledger.
    if not api_leagues:
        print("  Could not read the league list — the audit cannot rank "
              "candidates without it, so nothing was checked")
        return []

    catalogue = league_catalogue(api_leagues)
    summaries = walk_for_candidates(audited, catalogue, max_pages)

    resolutions = resolve_tier1_events(audited, summaries, teams)
    findings    = audit_findings(resolutions, ledger)

    print()
    print(format_audit_report(findings, tracked, opens, closes))
    print()

    # The daily run's own check, run here too: one league claimed by two events
    # is a mapping about to be evicted, and `recordable_resolutions` refuses to
    # act on it — but refusing silently would be its own kind of quiet.
    for problem in resolution_problems(ledger, resolutions):
        print(f"  ATTENTION: {problem}")

    record_resolutions(ledger, resolutions, findings, dry_run)

    print()
    print(format_audit_next_steps(findings))
    print("=" * 60)

    totals = audit_totals(findings, tracked)
    print(f"AUDIT SUMMARY: {totals['events']} event(s) — "
          f"{totals['covered']} covered, {totals['untracked']} awaiting a "
          f"verdict, {totals['declined']} declined, {totals['gap']} with no "
          f"league; {totals['mismatch']} disagreement(s) with the ledger, "
          f"{totals['unlisted']} tracked league(s) no event claims")

    return findings


def iso_date(text):
    """
    An ISO date off the command line, as the string the rest of the audit
    compares.

    Validated here rather than trusted, because the comparison downstream is a
    **string** comparison against Liquipedia's dates. `2025-1-1` is a natural
    typo and sorts below every real date, silently widening the range to the
    whole calendar — an error that produces a plausible-looking answer instead
    of a complaint.
    """
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not an ISO date — write it as YYYY-MM-DD"
        )


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Audit Tier 1 coverage against Liquipedia, in both "
                    "directions. Writes tier1_event onto the ledger; never a "
                    "verdict.",
    )
    parser.add_argument(
        "--from", dest="opens", metavar="YYYY-MM-DD", type=iso_date,
        help="first day of the period. Defaults to the dataset's earliest "
             "match — before that, an event is out of range rather than "
             "missing.",
    )
    parser.add_argument(
        "--to", dest="closes", metavar="YYYY-MM-DD", type=iso_date,
        help="last day of the period. Defaults to today; an event that has "
             "not started has nothing to find.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="report only — do not write tier1_event onto the ledger.",
    )
    parser.add_argument(
        "--max-pages", type=int, default=AUDIT_MAX_PAGES, metavar="N",
        help=f"cap on the pro match walk, in pages of 100 "
             f"(default {AUDIT_MAX_PAGES}).",
    )

    args = parser.parse_args(argv)

    # A backwards range selects no events, and no events is reported as
    # "nothing was checked" — which reads like an answer. Refuse it where the
    # mistake was made instead.
    if args.opens and args.closes and args.opens > args.closes:
        parser.error(f"--from {args.opens} is after --to {args.closes}")

    return args


def main(argv=None):
    args = parse_args(argv)

    run_audit(args.opens, args.closes, args.dry_run, args.max_pages)

    return 0


if __name__ == "__main__":
    sys.exit(main())
