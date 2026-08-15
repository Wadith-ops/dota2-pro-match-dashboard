# ADR-0013: Approval is a merged pull request, and refusal is a closed one

**Date:** 2026-08-15
**Status:** Accepted

## Context

Since `08` the pipeline recognises a Tier 1 tournament on the day of its first match: the resolver maps Liquipedia's event to an OpenDota league by date window, writes `tier1_event` onto the ledger record, and prints an `ATTENTION` line saying nobody has decided whether to cover it. Since `09` that queue is also a table on the dashboard's Upcoming tab.

Neither of those *is* a decision. The verdict in `data/leagues.json` stays `pending` until somebody edits one word, and the whole point of ADR-0001's design — coverage that does not depend on remembering to check Liquipedia — leaks out through that last step. The Esports World Cup was missing for two months because nobody looked; a line in a log nobody reads and a row in a tab nobody opens are the same failure with better instrumentation.

Three things constrain what the decision can be made *with*.

**The app cannot take it.** Streamlit Community Cloud deploys the dashboard from git onto an ephemeral filesystem, and the app is public. An approve button means a repository write token in a public app, and `09` ruled that out for one saved tap.

**The resolver must not take it either.** ADR-0009 is explicit: the resolver records what a league *is* and never what to do about it. A pipeline that activated the leagues it recognised would be fetching tournaments nobody approved, which is the automatic import ADR-0001 rejects.

**Refusal has to be recorded, not merely not-approved.** A `pending` league that is declined stays pending, so tomorrow's run finds it again and says the same thing. Without somewhere to write "no", the queue never empties and the ATTENTION line becomes noise for the second time.

## Decision

**A pull request is the approval mechanism. Merging is approval; closing is rejection.**

`propose_coverage.py` runs at the end of the six-hourly job. It reads the leagues awaiting a verdict — `core.awaiting_verdict`, the same queue the dashboard's tab and the run's ATTENTION lines read — builds a branch with each one's verdict set to `active`, and opens one pull request against `master`. Merging puts that verdict on `master` and the next scheduled run fetches the league with no further step. Closing it without merging fires `proposal-closed.yml`, which records `rejected`, so the league is never proposed again.

A pull request was chosen over a GitHub issue, a committed pending file, or an in-app button because it is literally "pending review", it is one tap on a phone, and both outcomes leave an audit trail of what was accepted and what was refused. It also carries the *diff*, so the thing being approved is the exact line that will change.

**One branch, therefore one open pull request.** `core.PROPOSAL_BRANCH` is a single fixed branch. A later run that finds a second league rebuilds that branch from today's `master` and updates the open pull request rather than opening a second review of the same question. A run whose queue matches what the open pull request already proposes does nothing at all — no push, no edit, no commit.

**The body is the record of what was proposed.** It carries, per league, the event and its window, the OpenDota league id and name, the match count, the share of team slots held by teams already in the dataset, whether the window was contested, and every candidate that lost under "also seen, not proposed". It ends with an HTML comment naming the league ids. That marker is how the closing workflow knows what to refuse: the branch may already be deleted by then, and the diff is a megabyte of ledger. A body somebody rewrote by hand names nothing, and nothing is recorded — the league stays pending and is proposed again on the next run.

**A verdict already on record is never overwritten by either path.** `core.record_verdicts` moves `pending` to `active` or `rejected` and refuses to move anything else, in either direction. Reversing a decision stays a human edit to one word, as it has been since `06`.

**Only the ledger is on the proposal branch.** The data push keeps going straight to `master` exactly as `13` left it. The one thing this writes to `master` is the pull request's URL onto `data/tier1_resolution.json`, which is what turns the Upcoming tab's Review link from "every open PR" into the pull request itself — and `core.tier1_resolution_state` carries that link forward, because the resolver rewrites the file and examines an event exactly once.

## Consequences

The queue can now empty. Both outcomes are terminal and both are recorded, so the ATTENTION line and the Upcoming tab describe a shrinking list rather than a standing one. Expect one or two proposals a month in steady state: the ledger is seeded by existence, so only genuinely new league ids are ever considered.

**The proposal branch is rebuilt, not added to.** What a reviewer needs is one commit against today's `master`, whatever the branch showed yesterday, so it is force-pushed. The cost is that a hand edit made *on* the branch — correcting a league's name before merging, which the body invites — is lost if a second league joins the proposal before the merge. That is rarer than reviewing an unreadable diff, and the name stays editable on `master` afterwards.

**`gh pr list` failing ends the run.** Everything else in this project degrades: a failed league list still fetches, a failed Liquipedia parse falls back to the committed calendar. Not knowing whether a pull request is already open is the one state that cannot be guessed from, because assuming none opens a second review of the same question. By that point the data has already been committed and pushed, so a refusal here costs a proposal and never a match.

**A gap cannot be proposed, and is reported instead.** A Tier 1 event with no OpenDota league has no ledger line to change, and a pull request with no diff cannot exist. Overdue gaps — the event is being played and this project has no data for it — are listed in the body of whatever proposal is open, because that is the page already in front of Wade. Upcoming ones are not: a tournament three months away, repeated in every proposal, is how a real gap stops being read. The full list stays on the Upcoming tab.

**This depends on a repository setting no code can assert.** Settings → Actions → General → "Allow GitHub Actions to create and approve pull requests" must be on, or `gh pr create` fails with 403. The run says so and the data push is unaffected. A pull request opened by `GITHUB_TOKEN` also does not trigger other workflows, so `tests.yml` does not run on it — the diff is one line of a data file, and the branch push itself is what the suite runs against.

**There are three writers of `master` now.** The six-hourly job, the workstation by hand, and the closing workflow. All three commit, then pull `--rebase --autostash`, then push; the closing workflow shares the job's `push-to-master` concurrency group, so it queues rather than racing.

**A closed proposal whose workflow fails is proposed again.** Nothing is lost, and the safe direction is the one taken: the league stays `pending` and turns up in the next run's queue. The same is true of a body rewritten by hand.

## Alternatives rejected

- **A GitHub issue.** No diff, so approving means editing the file afterwards anyway — which is the manual step this exists to remove.
- **A committed `pending.json` the pipeline reads.** Approval becomes editing a file to say what another file already says, and there is still nothing that means "no".
- **A button on the dashboard.** A repository write token in a public app on an ephemeral filesystem, for one saved tap (`09`).
- **A pull request per league.** Two tournaments in a fortnight is two branches, two reviews and two chances for a stale ledger diff to conflict. One branch makes "only one open at a time" a property of the design rather than something to check for.
- **Reading the closed branch's diff to learn what was refused.** The branch may be deleted on close, and the diff is a megabyte. The marker in the body is written by this project, survives the branch, and parses in one regex.
