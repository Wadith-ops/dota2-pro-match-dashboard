# ADR-0009: The resolver's candidate pool, and how candidates are ranked

**Date:** 2026-08-11
**Status:** Accepted

## Context

ADR-0001 fixed *what* the resolver does: a Liquipedia Tier 1 event resolves to the OpenDota league whose matches all fall inside its published window, and a contested window is settled by ranking candidates on the share of matches involving teams already in the dataset. Names are never compared.

It left three things open, all of which had to be settled to build it (`tier1-pipeline-automation/08`).

**Where candidates come from.** OpenDota has 10,050 leagues and its `/leagues` endpoint carries no dates, so nothing in the ledger says which leagues played in a given fortnight. Fetching a match list per league to find out is 10,050 calls against a 50,000-a-month budget.

**What "share of matches involving teams already in the dataset" counts.** Matches with at least one familiar team, matches with two, or team appearances. The three give different numbers and the design session's recorded figures had to be reproducible.

**Whether OpenDota's `tier` field may be read at all.** ADR-0001 rejects it as a *selector*: every league this project tracks is `professional`, one of 2,468. But the 2026 windows contain two leagues OpenDota marks `excluded` — BetBoom Streamers Battle 13 inside DreamLeague Season 29, and Asgard Championship inside 1win Essence II — and including them makes two more windows ambiguous than the design session found.

## Decision

**The candidate pool is walked from `/proMatches`, then confirmed league by league.** The pipeline pages backwards through the pro match list to the earliest event awaiting resolution — started, unmapped, and inside the lookback — which yields every league that played inside a window along with the team ids the ranking needs. Each league that then fits a window is re-read in full via `/leagues/{id}/matches` before it is allowed to win.

The confirmation pass is not belt-and-braces. A walk that stops at a date sees a league that began before that date as *narrower* than it is, and a narrow window is the direction that makes a league fit inside an event it has no business winning. Confirming from the full match list means a short walk costs a missed candidate — recorded as a gap — rather than a wrong answer.

**Overlap is a share of team slots, two per match.** A league of 60 matches has 120 slots; the score is how many of them are held by a team already in the dataset. A team playing ten matches counts ten times, and an anonymous team keeps its slot in the denominator, because a league of unidentifiable teams is exactly the league that should score low.

**Candidates rank on overlap, then on window coverage, then on match count, then on id.** Coverage is how much of the event's published window the league's matches span. It is there because **Tier 1 events nest inside one another** — a case ADR-0001 did not anticipate, because it never occurs in 2026. FISSURE PLAYGROUND 2 ran 23 October to 2 November 2025, entirely inside BLAST Slam IV's 14 October to 9 November. Both leagues are therefore candidates for BLAST Slam IV, both are made wholly of tracked teams and score 100%, and the nested event played *more* matches — 124 to 96 — in *half* the days. Ranking on volume gives BLAST Slam IV the wrong league and then leaves FISSURE PLAYGROUND 2 with none, since a league can only be one event. Coverage picks the league that ran the window, and both events resolve.

Coverage ranks and never gates, for the same reason overlap does not: a tournament resolves on the day of its first match, when it covers one day of a twelve-day window.

**`tier` prunes the pool but never picks the winner.** Leagues OpenDota marks `excluded` or `amateur` are dropped before ranking. This is a denylist, not an allowlist: a league with no tier, or with a value this project has never seen, stays a candidate.

**The answer is written in two places.** `tier1_event` on the ledger record is the durable mapping, one league at a time. `data/tier1_resolution.json` is the per-event view the dashboard's Upcoming tab (`09`) and the approval pull request (`15`) read, and it is built from the ledger rather than from the run that wrote it.

## Consequences

Resolution over the 2026 Tier 1 list reproduces the design session's result: six windows hold exactly one candidate, three hold two and the right league wins each, and four events are gaps. `tests/test_resolver.py::TestThe2026Season` asserts it league by league against 78 recorded leagues and 29,000 recorded matches.

Qualifiers still need no rule of their own, but ADR-0001's reason for it is only mostly right. Nine of the ten 2026 qualifier leagues never become candidates, because they run outside the main event's window. The tenth does: Road To EWC 2026 Regional Qualifiers ran 29 May to 5 June, inside BLAST SLAM VII's 26 May to 7 June, and it is the overlap ranking that beats it rather than the date. `tests/test_resolver.py` asserts both facts, because the claim is load-bearing and is not quite true as written.

The overlap figures reproduce to the decimal — 1win Essence II 85.8% against Games of the Future 9.0%, BLAST SLAM VI 100% against DreamLeague Division 2 Season 3 53.9%, the Esports World Cup 74.5% — which is what confirms the slot measure over the two per-match alternatives, neither of which can produce 85.8% from 60 matches. One figure differs: Road To EWC 2026 Regional Qualifiers scores **19.1%**, not the 22.1% the issue recorded. It loses to BLAST SLAM VII's 100% either way, and no other figure moves, so the issue's number is taken as a slip rather than a different measure.

A daily run costs nothing once its events are mapped. `events_awaiting_resolution` skips anything the ledger already maps and anything that has not started, so the walk has no reason to run at all in steady state — and when a tournament opens, it resolves on the day of its first match rather than the day after its final.

**Resolution is bounded to the last year, and that bound is load-bearing.** Liquipedia's calendar reaches back to 2005, and every event on it this project never mapped is, read literally, awaiting resolution. The first run of the resolver walked to its cap and read 12,900 pro matches for that reason. A year covers the dataset's own era; the back catalogue is `tier1-pipeline-automation/14`, a deliberate separate pass.

**The worst case is an event that starts and never resolves.** It stays awaiting for a year, so every run walks the full depth to look for it again — 259 pages, measured. At today's daily cadence that is 259 calls a day against a 50,000-a-month budget, which is affordable; at the six-hourly cadence `tier1-pipeline-automation/13` introduces it is roughly 31,000 a month, which is not comfortable. The cheap fix is a second, shorter horizon governing how long a *gap* keeps being retried, and it belongs with the change that makes it necessary rather than here.

**A league claimed by two events is reported, not resolved.** `apply_resolutions` keys its writes by league id, so a second claim would quietly evict the first and leave one event reading as a gap with no reason given. Nesting makes this reachable, so it is said out loud.

**The walk forced a rate-limit fix that this ADR did not anticipate.** A hundred consecutive calls at exactly one second apart sits *on* OpenDota's 60-a-minute limit rather than inside it, and the first real run was refused on every request after the walk — losing all twelve confirmations and reporting nine mapped tournaments as gaps. `fetch_url` now backs off on 429 and the inter-call delay is 1.1 seconds. Making the fetcher survive network failures generally is `tier1-pipeline-automation/12`; this is the part the resolver could not ship without.

Reading `tier` at all is a narrower use than ADR-0001 rejected, and the distinction is load-bearing: `professional` cannot say a league is Tier 1, but `excluded` is OpenDota stating a league is not a competitive event. Using it to *rank* or to *select* would reintroduce exactly what ADR-0001 ruled out. Pruning a streamer showmatch series out of a review queue does not.

The resolver never changes a verdict. It records what a league *is*; whether to cover it stays Wade's decision, taken by merging a pull request (`15`). Until that lands, a resolved league awaiting a verdict is printed as an `ATTENTION` line by the run that found it.

## Alternatives rejected

- **A match list per league** — 10,050 calls to answer a question `/proMatches` answers in twenty.
- **The `/explorer` SQL endpoint** — one call returns every league's window, but it is arbitrary SQL against OpenDota's database, and a scheduled job depending on it is a query away from breaking on a schema change. It was used once, by hand, to record the test fixture.
- **Overlap as a share of matches with a familiar team** — cannot produce 85.8% from 60 matches, and treats a match between two tracked teams as no stronger a signal than a match with one.
- **Distinct teams rather than appearances** — a team that played one match of a tournament would count as much as one that played the final.
- **An allowlist of `premium` and `professional`** — a Tier 1 league OpenDota left untagged would vanish from the pool silently, which is the failure this project exists to end. Over-including produces an ambiguity somebody reads.
- **Recording gaps as ledger rows with a `pending` verdict** — rejected in ADR-0008 and still wrong: a Tier 1 event with no league is coverage this project wants, not an undecided league.
