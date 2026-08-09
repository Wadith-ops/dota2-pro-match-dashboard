# ADR-0003: Negative first-blood times are valid data

**Date:** 2026-08-09
**Status:** Accepted
**Supersedes:** the "pre-game artefact" rule in `CLAUDE.md` and the corresponding `CONTEXT.md` invariant

## Context

`CLAUDE.md` carried a non-negotiable rule: *filter `first_blood_time_mins < 0` before any chart using it — negative values are pre-game artefacts, not fast first bloods.* `CONTEXT.md` stated the same as an invariant: *`first_blood_time_mins < 0` is invalid data.*

133 of 1,605 matches — 8.3% — carry a negative first-blood time, so the rule was discarding a material share of the data from every first-blood metric.

Nobody had checked the distribution.

## Decision

**Negative first-blood times are valid and are retained.** The rule is reversed.

## Consequences

The distribution is unambiguous. Every negative value falls between **−0.9 and −0.1 minutes**, with nothing below −1.0:

```
[-1.0, -0.5)   53 matches
[-0.5,  0.0)   80 matches
```

That is the pre-horn window exactly. In Dota 2 the match clock starts at zero at the horn, and teams contest runes and wards in the preceding seconds. A kill at −0.4 minutes is a real first blood that happened 24 seconds before the horn — not a clock artefact.

Had these been artefacts, the distribution would show a scatter of implausible values. It shows a tight, physically meaningful band against a hard floor.

First-blood figures on the dashboard **will change** when these 133 matches rejoin the averages. That is the correction, not a regression.

Consumers no longer filter on this column, which removes a rule every new chart had to remember.

The related pipeline bug is separate and still real: `if raw_time else None` converts a legitimate `first_blood_time` of exactly 0 into null, which has affected two matches. That is a falsy-zero bug, not a data-validity question.

## Notes

Recorded as an ADR specifically because this is the kind of finding that gets "fixed" again in a year. Anyone reading a negative time in a duration column will assume corruption; the evidence that it is not lives here.
