# ADR-0011: What the fetcher retries, and what a run reports

**Date:** 2026-08-11
**Status:** Accepted

## Context

The fetcher was written for a run somebody was watching. Every call went out with no timeout, so a connection that hung stalled the run until the terminal was noticed. A failure of any kind — a dropped connection, a gateway error, a missing match — was printed and answered `None`, and only a 429 was ever asked again (`tier1-pipeline-automation/08`, added because the resolver's first live run was refused on twelve consecutive calls).

That is survivable when a person is present. ADR-0004 moves the pipeline to a six-hourly GitHub Actions schedule, where nobody is: a hung call is a job that never ends and a schedule that never runs again, and a transient failure is a tournament quietly missing from the dataset until somebody asks why a team has no matches.

The run also had no account of itself. The scheduled job logged the last line of the pipeline's stdout, which is a column name — every entry for two months read `Pipeline: first_blood_time_mins`. A quiet run, a run that fetched a whole tournament and a run whose every league was unreachable all logged the same thing.

## Decision

**Every HTTP call carries an explicit timeout.** `REQUEST_TIMEOUT_SECONDS` is 30, the same number `liquipedia.py` already used — one number for the project, not one per client. `get_patch_map` was the last call making its own request; it goes through `fetch_url` now and inherits all of this.

**What is worth asking again is decided by a status code, so it lives in the core.** `core.retry_pause(status, attempt)` answers with a number of seconds or with None, and it is the whole policy — the same shape as `classify_fetch`, which holds the whole re-fetch policy. The shell asks, sleeps and gives up; it decides nothing.

**Two schedules, because there are two failures.** A **429** means the minute's allowance is spent and the minute has to pass: 30s, 60s, 120s. A **timeout, a dropped connection or a 5xx** is not a refusal — the next call usually works: 2s, 5s, 15s. A request that never got a status is handed to the policy as `None` and is transient by definition, because something below the application answered.

**Every schedule ends.** Four attempts, then the call fails and the run carries on. A run that misses a match fetches it next time; a run that never finishes fetches nothing ever again. The budget is **per call, not per kind**: a call refused once and then timing out has three attempts left, not six. What is worth guaranteeing is that no single call can hold the run open, and per-kind allowances would weaken exactly that.

**Nothing raises out of `fetch_url`.** The retry path catches `requests.RequestException`; a second, non-retrying catch answers None to anything else. `requests` mostly wraps what it raises but not invariably, and one league's fetch raising is one league's fetch ending the run — while an error nobody anticipated is no evidence that asking again would help.

**No single failure ends a run.** A league whose match-id list fails is recorded as failed and the loop moves on. A match whose detail call fails is counted and, crucially, *not* checkpointed — the same mechanism a held match uses, where the absence of a checkpoint entry is the retry.

**A run keeps one record per league and reports it.** `core.league_run_record` is the shape: what the league had, how much of it was already fetched, the three fetch verdicts, failed matches, and whether the list could be read at all. The run prints them as a table, and prints one `RUN SUMMARY:` line at the very end.

**A failed league is named in that line, not merely counted.** The table says which one too, but the table is stdout and the line is what reaches the log. "One league list failed" recorded where nobody was watching is a question; the answer is which tournament stopped being covered, which is the two-month failure this whole feature exists to shorten.

**That line is the contract with the scheduled job.** `auto_update.py` finds it with `core.read_run_summary` rather than taking the last line of output. It is printed after everything that could fail, so its presence means the run reached the end and its absence means it did not.

## Consequences

A run that fetches nothing now reads differently from a run that failed, in the log, without opening a terminal — which is the whole point of moving the job somewhere nobody is watching.

**A league that could not be reached is no longer indistinguishable from a league with no matches.** Both used to end the run having fetched nothing and reporting nothing; the difference is now on the record and in the summary line. That distinction is the one whose absence hid the Esports World Cup for two months.

Worst-case run time goes up. A pathological run — every one of fifteen leagues timing out on every attempt — spends four attempts and 22 seconds of backoff per call before moving on, which is minutes rather than seconds. That is the right trade at a six-hourly cadence and it is bounded, which is what matters: the schedule cannot be held open indefinitely by a dead endpoint.

Retrying a 5xx costs calls against the 50,000-a-month allowance. At three extra calls per genuine failure and failures being rare, this is noise against a dataset of 1,822 matches fetched once each.

`fetch_url` takes the policy as a parameter, so a caller that cannot afford to wait can pass a stricter one. Nothing does today; the tests do, which is what keeps the suite instant.

## Alternatives rejected

- **One retry schedule for everything.** Either the rate limit gets a 2-second backoff and is refused again, or a dead network gets the 429 schedule and every call costs three and a half minutes. There are two failures here and one schedule cannot serve both.
- **Retrying everything, including 404s.** A missing match will still be missing in two minutes. Retrying it spends the allowance on a certainty.
- **Unbounded retries with exponential backoff.** The failure mode is a job that never returns, which on a schedule is worse than a run that fetched nothing: the next run cannot start.
- **A structured run report written to a file for the scheduled job to read.** A second artifact, a second path to keep in step, and a file to clean up — to carry one line between two processes that already share stdout.
- **Making the job parse the whole of stdout.** The pipeline would then be unable to change what it prints. One prefixed line is a contract; the rest of the output stays a human's to read.
