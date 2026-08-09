# ADR-0004: The pipeline runs in GitHub Actions, and approval happens by pull request

**Date:** 2026-08-09
**Status:** Accepted

## Context

The pipeline ran as a Windows scheduled task (`Dota2DataUpdate`) on Wade's workstation, driving a Python interpreter from a shared virtual environment outside the project. Coverage therefore depended on one machine being switched on, and the runtime environment was not reproducible from `requirements.txt`.

The daily job's only pipeline log line was the last line of stdout, which is a column name — every entry for two months read `Pipeline: first_blood_time_mins`. The log is local and gitignored, so a failure had no path to Wade's attention.

Two mechanisms for approving newly-detected leagues were considered alongside the move, because approval has to work when the job runs somewhere Wade is not sitting.

## Decision

**The pipeline runs in GitHub Actions on a six-hour schedule.** The workstation scheduled task is disabled; `auto_update.py` stays runnable by hand.

**Approval is a pull request.** CI opens a PR proposing a ledger change; merging is approval, closing is rejection.

**The dashboard gets a read-only Upcoming tab**, not an approve button.

**API keys are repository secrets** for both OpenDota and Liquipedia.

## Consequences

Six hours means a tournament evening's matches are present before Wade next looks, and it composes with the suspect-match retry policy in ADR-0002's sibling work — an unparsed replay gets four attempts a day instead of one.

Only one writer may push to `master`. Retiring the scheduled task is a requirement of this decision, not a tidy-up: two writers will eventually collide.

This decision is **blocked by the artifact split** (ADR-0002) for a hard technical reason. The flatten step reads `data/matches.json`, which cannot exist on a hosted runner. The ordering is forced, not preferred.

Streamlit Cloud only picks up files on redeploy, so CI must keep bumping the `# data:` comment in `dashboard.py`. The `ttl` on `load_data()` is not a substitute — an expiring cache re-reads the same stale file inside the container.

CI must commit only its own outputs. The current scripts run `git add dashboard.py`, which would sweep up any uncommitted local edit and deploy it.

An OpenDota key is taken despite the free tier being keyless, because limits are enforced per IP and Actions runners have shared, rotating addresses.

Liquipedia's v3 API is a commercial product with site plans and pricing, so it is not expected to be part of this system. How the Tier 1 list is obtained — the MediaWiki endpoint under their terms of use, or a manually transcribed calendar in the ledger — is an open question in `tier1-pipeline-automation/07`. The resolver is identical either way, so this does not block the migration.

## Alternatives rejected

- **An approve button in the Streamlit app.** Community Cloud is deployed from git with an ephemeral filesystem and cannot write back to the repository. A button would require a repository write token inside a public app — a real credential risk to save one tap, when tournaments are known days or weeks ahead.
- **Approval via a GitHub issue or a committed pending file.** Both work, but a PR *is* pending review: one tap on a phone, an audit trail of accepts and rejects, and no new tooling.
- **Keeping the local scheduled task as a fallback alongside CI.** Two writers pushing to `master`.
