"""
What changes when the run is unattended.

Two things, and they exist for the same reason: from issue 13 the pipeline runs
on a GitHub Actions runner every six hours rather than on Wade's workstation
once a day.

- **The API key.** OpenDota enforces its rate limit per IP, and a runner's
  address is shared and rotating, so a keyless run can meet somebody else's
  limit mid-tournament. The key is optional — a local run has no secret and
  must not need one.
- **The resolver's gate.** Resolution is a whole-day question, so running it
  four times a day buys nothing and costs 4× the walk it makes when an event
  never resolves. The gate is what keeps the six-hour cadence affordable.

Nothing here touches the network or the clock.
"""
import json
from datetime import datetime, timezone

import pytest
import requests

import core
import opendota_pipeline as pipeline


def at(when):
    """A UTC moment, written the way a run's clock would read it."""
    return datetime.strptime(when, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)


# ── The API key ───────────────────────────────────────────────────────────────

OPENDOTA = "https://api.opendota.com/api"
PATCH_LIST = "https://www.dota2.com/datafeed/patchnoteslist"


class TestWhichUrlsCarryTheKey:
    """
    `core.keyed_url` is the whole decision. It is a string transform, so it
    lives in the core and is tested against plain data — the shell only makes
    the request.
    """

    def test_an_opendota_url_carries_the_key(self):
        assert core.keyed_url(
            f"{OPENDOTA}/matches/123", "secret", OPENDOTA
        ) == f"{OPENDOTA}/matches/123?api_key=secret"

    def test_a_url_that_already_has_a_query_gets_another_parameter(self):
        # `/proMatches?less_than_match_id=...` is most of the resolver's walk.
        assert core.keyed_url(
            f"{OPENDOTA}/proMatches?less_than_match_id=9", "secret", OPENDOTA
        ).endswith("?less_than_match_id=9&api_key=secret")

    def test_no_key_leaves_the_url_alone(self):
        # The free tier is keyless, and a local run has no secret. Degrading to
        # keyless is what makes the secret optional rather than a prerequisite.
        for absent in (None, "", "   "):
            assert core.keyed_url(f"{OPENDOTA}/matches/123", absent,
                                  OPENDOTA) == f"{OPENDOTA}/matches/123"

    def test_another_vendor_never_sees_the_key(self):
        # Valve's patch list is the one call that goes elsewhere. Sending an
        # OpenDota credential to a third party is a leak, not a no-op.
        assert core.keyed_url(PATCH_LIST, "secret", OPENDOTA) == PATCH_LIST

    def test_an_unknown_base_keys_nothing(self):
        # Every URL starts with the empty string. A base that got lost would
        # otherwise send the key to whatever the caller asked for next.
        assert core.keyed_url(f"{OPENDOTA}/matches/1", "secret", "") == \
            f"{OPENDOTA}/matches/1"

    def test_the_key_is_escaped(self):
        # It arrives from an environment variable, which is not a promise that
        # it is URL-safe.
        assert core.keyed_url(f"{OPENDOTA}/x", "a b&c=d", OPENDOTA) == \
            f"{OPENDOTA}/x?api_key=a%20b%26c%3Dd"

    def test_a_key_with_a_trailing_newline_still_works(self):
        # `OPENDOTA_API_KEY=$(cat key.txt)` and any number of other ways.
        assert core.keyed_url(f"{OPENDOTA}/x", " secret\n", OPENDOTA) == \
            f"{OPENDOTA}/x?api_key=secret"


@pytest.fixture
def transport(monkeypatch):
    """
    Replaces `requests.get` and `time.sleep`, recording the URL each call was
    actually made against — which is the thing under test here.
    """
    class Response:
        def __init__(self, status_code):
            self.status_code = status_code

        def json(self):
            return {"ok": True}

    class Stub:
        def __init__(self):
            self.answers = []
            self.urls    = []

        def get(self, url, **kwargs):
            self.urls.append(url)
            answer = self.answers.pop(0) if self.answers else 200

            if isinstance(answer, Exception):
                raise answer

            return Response(answer)

    stub = Stub()
    monkeypatch.setattr(requests, "get", stub.get)
    monkeypatch.setattr(pipeline.time, "sleep", lambda seconds: None)
    return stub


class TestTheFetcherUsesTheKey:
    """
    Every OpenDota call goes through `fetch_url`, so this is the only place the
    key has to be applied — and the only place it could leak.
    """

    def test_the_key_is_added_to_the_request(self, transport, monkeypatch):
        monkeypatch.setattr(pipeline, "OPENDOTA_API_KEY", "secret")

        pipeline.fetch_url(f"{OPENDOTA}/matches/123")

        assert transport.urls[0].endswith("?api_key=secret")

    def test_a_run_with_no_key_asks_for_exactly_what_it_was_given(
        self, transport, monkeypatch
    ):
        monkeypatch.setattr(pipeline, "OPENDOTA_API_KEY", "")

        pipeline.fetch_url(f"{OPENDOTA}/matches/123")

        assert transport.urls == [f"{OPENDOTA}/matches/123"]

    def test_valves_patch_list_is_fetched_without_it(self, transport, monkeypatch):
        monkeypatch.setattr(pipeline, "OPENDOTA_API_KEY", "secret")

        pipeline.fetch_url(pipeline.PATCH_LIST_URL)

        assert transport.urls == [pipeline.PATCH_LIST_URL]

    def test_a_failure_is_reported_without_the_key(
        self, transport, monkeypatch, capsys
    ):
        # The runner's log is public on a public repository. Actions masks a
        # secret it recognises, but a credential that was never printed needs
        # no masking — and the un-keyed URL is the more readable line anyway.
        monkeypatch.setattr(pipeline, "OPENDOTA_API_KEY", "secret")
        transport.answers = [404]

        pipeline.fetch_url(f"{OPENDOTA}/matches/123")

        assert "secret" not in capsys.readouterr().out

    def test_a_retry_is_reported_without_the_key(
        self, transport, monkeypatch, capsys
    ):
        monkeypatch.setattr(pipeline, "OPENDOTA_API_KEY", "secret")
        transport.answers = [429, 200]

        pipeline.fetch_url(f"{OPENDOTA}/matches/123")

        assert "secret" not in capsys.readouterr().out

    def test_a_dropped_connection_is_reported_without_the_key(
        self, transport, monkeypatch, capsys
    ):
        # `requests` embeds the URL it was *given* in the text of what it
        # raises, so reporting `url` rather than `requested` is not enough on
        # its own. The scheduled job tees its output to a file it uploads as an
        # artifact, and that copy is written before GitHub masks anything.
        monkeypatch.setattr(pipeline, "OPENDOTA_API_KEY", "secret")
        transport.answers = [requests.ConnectionError(
            "HTTPSConnectionPool(host='api.opendota.com', port=443): Max "
            "retries exceeded with url: /api/matches/123?api_key=secret"
        ), 200]

        pipeline.fetch_url(f"{OPENDOTA}/matches/123")

        printed = capsys.readouterr().out
        assert "secret" not in printed
        assert "api_key=<redacted>" in printed

    def test_an_unexpected_error_is_reported_without_the_key(
        self, monkeypatch, capsys
    ):
        monkeypatch.setattr(pipeline, "OPENDOTA_API_KEY", "secret")

        def explode(url, **kwargs):
            raise RuntimeError(f"nobody anticipated {url}")

        monkeypatch.setattr(requests, "get", explode)

        pipeline.fetch_url(f"{OPENDOTA}/matches/123")

        assert "secret" not in capsys.readouterr().out


class TestRedactingTheKey:
    def test_the_value_goes_and_the_parameter_stays(self):
        assert core.without_api_key("...url: /api/x?api_key=abc123") == \
            "...url: /api/x?api_key=<redacted>"

    def test_a_parameter_after_it_survives(self):
        assert core.without_api_key("?api_key=abc&less_than_match_id=9") == \
            "?api_key=<redacted>&less_than_match_id=9"

    def test_an_escaped_key_goes_too(self):
        # By pattern rather than by knowing the key, so a URL-escaped one and a
        # key this process never saw are both covered.
        assert "a%20b" not in core.without_api_key("?api_key=a%20b)")

    def test_text_with_no_key_in_it_is_untouched(self):
        assert core.without_api_key("connection reset") == "connection reset"


# ── Running without the machine's own state ───────────────────────────────────


def stored(match_id, start_time, parsed=True):
    """A Standard record, as thin as `core.suspect_reasons` will accept."""
    record = {
        "match_id": match_id,
        "start_time": start_time,
        "duration": 2400,
        "radiant_score": 30,
        "dire_score": 25,
        "objectives": [{"type": "building_kill", "time": 600,
                        "key": "npc_dota_goodguys_tower1_mid"}],
    }

    if not parsed:
        # An unparsed replay arrives with no objectives at all, which is what
        # makes every objective column a zero meaning "unknown". See ADR-0007.
        record["objectives"] = []

    return record


class TestRebuildingTheCheckpointFromTheStore:
    """
    A runner has no `checkpoints/` directory — it is machine state and is not
    committed. Without this the first run on a fresh machine re-fetches all
    1,822 matches and appends every one of them to the committed store a second
    time, which is 37 MB of duplicate lines per cache miss.

    The store is the record of what was fetched, so the checkpoint is derived
    from it by the **same rule the live loop uses** — `classify_fetch` — rather
    than by membership. A held match is in the store and deliberately not in the
    checkpoint, and deriving the two apart is what keeps the re-fetch working.
    """

    def test_a_complete_match_counts_as_fetched(self):
        now = at("2026-08-14 00:00")

        assert core.checkpoint_from_store(
            [stored(1, now.timestamp() - 86400)], now
        ) == {1}

    def test_a_match_still_worth_re_fetching_does_not(self):
        # Suspect and young: the absence from the checkpoint *is* the re-fetch.
        now = at("2026-08-14 00:00")

        assert core.checkpoint_from_store(
            [stored(1, now.timestamp() - 86400, parsed=False)], now
        ) == set()

    def test_a_match_given_up_on_counts_as_fetched(self):
        # Suspect and out of time. Re-fetching it every six hours forever
        # spends calls on a replay that is never going to be parsed.
        now = at("2026-08-14 00:00")

        assert core.checkpoint_from_store(
            [stored(1, now.timestamp() - 30 * 86400, parsed=False)], now
        ) == {1}

    def test_a_record_with_no_match_id_is_ignored(self):
        assert core.checkpoint_from_store([{"start_time": 1}],
                                          at("2026-08-14 00:00")) == set()

    def test_an_empty_store_gives_an_empty_checkpoint(self):
        for empty in ([], None):
            assert core.checkpoint_from_store(empty,
                                              at("2026-08-14 00:00")) == set()


class TestTheShellRecoversTheCheckpointOnlyWhenItHasTo:
    @pytest.fixture(autouse=True)
    def store(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pipeline, "MATCH_CHECKPOINT",
                            str(tmp_path / "fetched_matches.json"))
        monkeypatch.setattr(pipeline, "STANDARD_FILE",
                            str(tmp_path / "matches_standard.jsonl"))
        return tmp_path

    def test_an_existing_checkpoint_is_used_as_it_stands(self, store):
        # Reading a 37 MB store to answer a question the checkpoint already
        # answers is a second full parse for nothing.
        pipeline.save_checkpoint(pipeline.MATCH_CHECKPOINT, [7])
        (store / "matches_standard.jsonl").write_text(
            json.dumps(stored(1, 1_700_000_000)) + "\n", encoding="utf-8"
        )

        assert pipeline.fetched_checkpoint(at("2026-08-14 00:00")) == {7}

    def test_a_missing_checkpoint_is_rebuilt_from_the_store(self, store):
        (store / "matches_standard.jsonl").write_text(
            json.dumps(stored(1, 1_700_000_000)) + "\n", encoding="utf-8"
        )

        assert pipeline.fetched_checkpoint(at("2026-08-14 00:00")) == {1}

    def test_a_first_run_anywhere_has_nothing_to_rebuild_from(self):
        assert pipeline.fetched_checkpoint(at("2026-08-14 00:00")) == set()

    def test_what_was_rebuilt_is_written_back(self, store):
        # Otherwise nothing writes it: the checkpoint is saved by
        # `run_pipeline`'s flush, and a run that fetches no new matches never
        # flushes — so a runner would re-parse the whole 37 MB store every run
        # and cache a `checkpoints/` directory with no checkpoint in it.
        (store / "matches_standard.jsonl").write_text(
            json.dumps(stored(1, 1_700_000_000)) + "\n", encoding="utf-8"
        )

        pipeline.fetched_checkpoint(at("2026-08-14 00:00"))

        assert pipeline.load_checkpoint(pipeline.MATCH_CHECKPOINT) == {1}


# ── The resolver's daily gate ─────────────────────────────────────────────────


class TestWhenResolutionIsDue:
    """
    `core.resolution_due` compares whole UTC days, because that is the grain
    the resolver already works in: `events_awaiting_resolution` takes a date,
    and ADR-0009's goal is that an event resolves on the day of its first
    match. A once-daily resolver does that exactly as well as a six-hourly one.
    """

    def test_a_run_that_has_never_resolved_is_due(self):
        assert core.resolution_due(None, "2026-08-14") is True

    def test_a_second_run_on_the_same_day_is_not(self):
        assert core.resolution_due("2026-08-14", "2026-08-14") is False

    def test_the_next_day_is_due_again(self):
        assert core.resolution_due("2026-08-13", "2026-08-14") is True

    def test_a_marker_from_the_future_is_due_rather_than_stuck(self):
        # A clock that ran ahead — or a marker written by another machine —
        # must not switch the resolver off until the date catches up. Running
        # rewrites the marker, so this self-corrects on the first run.
        assert core.resolution_due("2026-09-01", "2026-08-14") is True

    def test_an_unreadable_marker_is_due(self):
        # The safe direction: an extra walk, never a skipped one.
        for damaged in ("", "not a date", 20260814, {"last_run": "2026-08-14"}):
            assert core.resolution_due(damaged, "2026-08-14") is True


class TestTheMarkerSurvivesBetweenRuns:
    @pytest.fixture(autouse=True)
    def marker(self, tmp_path, monkeypatch):
        path = tmp_path / "last_resolution.json"
        monkeypatch.setattr(pipeline, "RESOLUTION_CHECKPOINT", str(path))
        return path

    def test_no_marker_reads_as_never(self):
        assert pipeline.read_last_resolution() is None

    def test_what_is_recorded_is_what_is_read_back(self):
        pipeline.record_resolution_run("2026-08-14")

        assert pipeline.read_last_resolution() == "2026-08-14"

    def test_a_damaged_marker_reads_as_never(self, marker):
        marker.write_text("{not json", encoding="utf-8")

        assert pipeline.read_last_resolution() is None

    def test_a_marker_of_the_wrong_shape_reads_as_never(self, marker):
        marker.write_text(json.dumps(["2026-08-14"]), encoding="utf-8")

        assert pipeline.read_last_resolution() is None

    def test_the_gate_lets_the_first_run_of_a_day_through_and_no_other(
        self, monkeypatch
    ):
        # `resolve_if_due` is the seam: the gate is one decision and the work
        # behind it costs a 259-page walk, so what is asserted here is how many
        # times that work is reached.
        ran = []

        def resolved(ledger, api_leagues, teams, now):
            ran.append(now)
            return True

        monkeypatch.setattr(pipeline, "resolve_tier1_leagues", resolved)

        morning   = at("2026-08-14 02:00")
        midday    = at("2026-08-14 14:00")
        tomorrow  = at("2026-08-15 02:00")

        for now in (morning, midday, tomorrow):
            pipeline.resolve_if_due({}, [], set(), now)

        assert ran == [morning, tomorrow]

    def test_a_skipped_run_says_so(self, monkeypatch, capsys):
        # A run that quietly did nothing and a run that resolved nothing read
        # the same in a log, which is the confusion this whole project keeps
        # paying for.
        monkeypatch.setattr(pipeline, "resolve_tier1_leagues",
                            lambda *args: True)
        pipeline.resolve_if_due({}, [], set(), at("2026-08-14 02:00"))

        capsys.readouterr()
        pipeline.resolve_if_due({}, [], set(), at("2026-08-14 14:00"))

        assert "2026-08-14" in capsys.readouterr().out

    def test_a_resolver_that_could_not_ask_does_not_spend_the_day(
        self, monkeypatch
    ):
        # The failure paths inside `resolve_tier1_leagues` return normally: no
        # league list this run, or a walk that read no pro matches. Recording
        # the day there would let one failed `/leagues` fetch stand the
        # resolver down until tomorrow — and a tournament starting today would
        # be recognised a day late, which is what ADR-0009 exists to prevent.
        ran = []

        def could_not_ask(ledger, api_leagues, teams, now):
            ran.append(now)
            return False

        monkeypatch.setattr(pipeline, "resolve_tier1_leagues", could_not_ask)

        pipeline.resolve_if_due({}, None, set(), at("2026-08-14 02:00"))
        pipeline.resolve_if_due({}, None, set(), at("2026-08-14 08:00"))

        assert len(ran) == 2
        assert pipeline.read_last_resolution() is None

    def test_deleting_the_marker_is_how_a_run_is_forced(self, marker):
        # There is no flag for this on purpose. Removing a checkpoint to make
        # the next run do the work again is the idiom the pipeline already uses
        # for re-fetching a league's matches.
        pipeline.record_resolution_run("2026-08-14")
        marker.unlink()

        assert core.resolution_due(pipeline.read_last_resolution(),
                                   "2026-08-14") is True
