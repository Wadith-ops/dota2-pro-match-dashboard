# ADR-0006: The Tier 1 list is read from Liquipedia's MediaWiki API, with a committed calendar as the fallback

**Date:** 2026-08-09
**Status:** Accepted

Resolves the transport question ADR-0001 left open. ADR-0001 fixes *what* Tier 1 means and *how* leagues resolve to events; this fixes *how the list is obtained*.

## Context

ADR-0001 defined the dataset as Liquipedia's Tier 1 Tournaments list but deliberately did not say how to read it, because the resolver is identical either way. Three transports were on the table.

**The v3 API (`api.liquipedia.net`) is a commercial product.** Site plans and pricing, an API key, money for a personal project.

**The MediaWiki API (`liquipedia.net/dota2/api.php`) is a different endpoint** — the standard wiki API every MediaWiki install exposes. No key. It was previously assumed to be of uncertain legitimacy because Liquipedia sells the other one.

**A manually transcribed calendar** — roughly 13 entries a year — takes no dependency at all.

The blocking question was whether Liquipedia's terms permit automated non-commercial use of the free endpoint. An earlier attempt to check hit a Cloudflare challenge on the *pricing* page and generalised from it, concluding the terms were unreadable. They are not: `liquipedia.net/api-terms-of-use` serves fine, and says Liquipedia is "pleased to provide free access to the information in our wikis through the MediaWiki API". There is no restriction to paying users.

## Decision

**Read the list from the MediaWiki API**, `action=parse&prop=text` against the Tier 1 Tournaments page, parsing the rendered tournaments table.

**Honour the four conditions of that access as code, not intent:**

| Condition | Where it lives |
|---|---|
| Descriptive User-Agent with contact details | `liquipedia.USER_AGENT` |
| `action=parse` at most once per 30s | `liquipedia.ParseRateLimiter` |
| Cache results; do not re-request unchanged data | `liquipedia.get_tier1_events`, 24-hour cache |
| CC BY-SA 3.0 attribution where displayed | `core.LIQUIPEDIA_ATTRIBUTION`, rendered by issue 09 |

**Ship the transcribed calendar as the fallback, not the replacement.** `data/tier1_calendar.json` is committed and holds every event on the page. It is generated from the same authoritative table rather than typed by hand — same content, no transcription errors — but its role is the manual option's: a real list to keep working from when Liquipedia is unreachable.

**Degrade one step at a time.** Fresh cache → network → any cache however old → committed calendar. `get_tier1_events` returns the source alongside the events, because "Liquipedia is down" and "Liquipedia lists no events" are different facts and must not look alike to the caller.

**Parse the rendered table only.** Restated from ADR-0001 because it now has an executable form: the parser keys on the `table2__row--body` row class and the `column__tournament` cell class, and reads the Timeline template nowhere.

## Consequences

The pipeline discovers new Tier 1 events without anyone transcribing them, which is the point of the feature — a hardcoded list that cannot discover a tournament is what hid the Esports World Cup for two months.

One parse call per daily run sits far inside a 30-per-hour allowance. The limiter exists for issue 14, which walks several years of the page in one run.

**We take a dependency on Liquipedia's HTML structure**, which can change without notice. This is the real cost of choosing the API over transcription, and it is bounded rather than eliminated: a markup change yields zero parsed rows, which is treated as a failed fetch and falls back, so the failure mode is a stale calendar rather than an empty one. A 200 response carrying unrecognised markup is explicitly *not* read as "there are no Tier 1 events".

The fallback calendar goes stale from the day it is committed. It is a floor, not a source of truth — an event added to Liquipedia after that date is invisible while the fetch is broken. What catches a genuinely new tournament in that window is not this ADR but issue 06's `pending` verdict on unrecognised OpenDota league ids, surfaced by issue 09.

Attribution is now an obligation the dashboard carries, not only the pipeline. Any view built on calendar data has to render it.

Liquipedia writes a new-year crossing with both years spelled out (`Nov 28, 2014 – Jul 05, 2015`), never as a single trailing year. The parser handles the single-year form too, defensively, because that shape would otherwise record an event as running backwards by eleven months.

## Alternatives rejected

- **Paid v3 API** — structured and supported, but costs money for a personal project, and the free endpoint is explicitly offered for this use.
- **Manual calendar as the only source** — the transcription is cheap, but it leaves the pipeline unable to discover anything, which is the defect the feature exists to fix. Kept as the fallback instead.
- **Parsing the page's wikitext** — yields the Timeline template, which deliberately includes Tier 2 events. See ADR-0001.
- **Treating an unparseable page as an empty list** — would report every tournament as missing simultaneously the first time Liquipedia changes a class name.
