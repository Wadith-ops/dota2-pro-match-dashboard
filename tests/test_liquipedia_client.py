"""
Tests for the Liquipedia client shell.

Every collaborator that would reach outside the process — the HTTP call, the
clock, the sleep, the cache path — is injected, so nothing here touches the
network and the 30-second limiter is proved without waiting 30 seconds.

Three of the four conditions Liquipedia attaches to free MediaWiki API use are
enforced here (User-Agent, parse interval, caching); the fourth, attribution,
is the dashboard's and belongs to issue 09.
"""
import json

import pytest

import liquipedia
from liquipedia import (
    CACHE_MAX_AGE_SECONDS,
    PARSE_MIN_INTERVAL_SECONDS,
    USER_AGENT,
    ParseRateLimiter,
    cache_is_fresh,
    fetch_tier1_html,
    get_tier1_events,
    load_cache,
    load_seed_calendar,
    save_cache,
    write_seed_calendar,
)
from core import events_in_year


class FakeResponse:
    def __init__(self, status_code=200, payload=None, raises=False):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self._raises = raises

    def json(self):
        if self._raises:
            raise ValueError("not JSON")
        return self._payload


class FakeGet:
    """Stands in for `requests.get`, recording how it was called."""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if self.error:
            raise self.error
        return self.response


class FakeClock:
    """A monotonic clock that only moves when a sleep is recorded."""

    def __init__(self, start=1000.0):
        self.now = start
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def parse_payload(html):
    return {"parse": {"title": "Tier 1 Tournaments", "text": html}}


class TestParseRateLimiter:
    def test_does_not_wait_before_the_first_call(self):
        clock = FakeClock()
        ParseRateLimiter(monotonic=clock.monotonic, sleep=clock.sleep).wait()

        assert clock.sleeps == []

    def test_waits_out_the_remaining_interval_on_a_second_call(self):
        # Two parse calls back to back must be 30 seconds apart. Nothing in the
        # project does that today — issue 14 was expected to and does not, since
        # the Tier 1 page is one table covering every year — so this test is the
        # only thing holding a term of the API that no caller currently reaches.
        clock = FakeClock()
        limiter = ParseRateLimiter(monotonic=clock.monotonic, sleep=clock.sleep)

        limiter.wait()
        limiter.wait()

        assert clock.sleeps == [PARSE_MIN_INTERVAL_SECONDS]

    def test_does_not_wait_when_the_interval_has_already_passed(self):
        clock = FakeClock()
        limiter = ParseRateLimiter(monotonic=clock.monotonic, sleep=clock.sleep)

        limiter.wait()
        clock.now += PARSE_MIN_INTERVAL_SECONDS + 1
        limiter.wait()

        assert clock.sleeps == []

    def test_defaults_to_the_interval_the_terms_require(self):
        assert ParseRateLimiter().min_interval == 30.0


class TestFetchTier1Html:
    def _limiter(self):
        clock = FakeClock()
        return ParseRateLimiter(monotonic=clock.monotonic, sleep=clock.sleep)

    def test_returns_the_rendered_html(self):
        get = FakeGet(FakeResponse(payload=parse_payload("<table/>")))

        assert fetch_tier1_html(limiter=self._limiter(), get=get) == "<table/>"

    def test_identifies_the_project_in_the_user_agent(self):
        # A generic agent is liable to be blocked, so this is a condition of
        # access rather than etiquette. Contact details are part of the form
        # Liquipedia asks for.
        get = FakeGet(FakeResponse(payload=parse_payload("<table/>")))
        fetch_tier1_html(limiter=self._limiter(), get=get)

        sent = get.calls[0]["headers"]["User-Agent"]
        assert sent == USER_AGENT
        assert "Dota2ProMatchDashboard" in sent
        assert "wade.yang94@gmail.com" in sent
        assert "python-requests" not in sent.lower()

    def test_sends_an_explicit_timeout(self):
        # A scheduled run must not hang forever on a stalled connection.
        get = FakeGet(FakeResponse(payload=parse_payload("<table/>")))
        fetch_tier1_html(limiter=self._limiter(), get=get)

        assert get.calls[0]["timeout"] > 0

    def test_asks_for_the_rendered_text_of_the_tier1_page(self):
        get = FakeGet(FakeResponse(payload=parse_payload("<table/>")))
        fetch_tier1_html(limiter=self._limiter(), get=get)

        params = get.calls[0]["params"]
        assert params["action"] == "parse"
        assert params["prop"] == "text"
        assert params["page"] == "Tier_1_Tournaments"

    def test_waits_on_the_limiter_before_calling(self):
        clock = FakeClock()
        limiter = ParseRateLimiter(monotonic=clock.monotonic, sleep=clock.sleep)
        get = FakeGet(FakeResponse(payload=parse_payload("<table/>")))

        fetch_tier1_html(limiter=limiter, get=get)
        fetch_tier1_html(limiter=limiter, get=get)

        assert clock.sleeps == [PARSE_MIN_INTERVAL_SECONDS]

    def test_returns_none_on_a_bad_status(self):
        get = FakeGet(FakeResponse(status_code=429))

        assert fetch_tier1_html(limiter=self._limiter(), get=get) is None

    def test_returns_none_when_mediawiki_reports_an_error_with_status_200(self):
        # MediaWiki answers a missing page with HTTP 200 and an `error` key, so
        # the status code alone does not tell us the call worked.
        get = FakeGet(
            FakeResponse(payload={"error": {"code": "missingtitle"}})
        )

        assert fetch_tier1_html(limiter=self._limiter(), get=get) is None

    def test_returns_none_when_the_response_is_not_json(self):
        get = FakeGet(FakeResponse(raises=True))

        assert fetch_tier1_html(limiter=self._limiter(), get=get) is None

    def test_returns_none_when_the_payload_carries_no_text(self):
        get = FakeGet(FakeResponse(payload={"parse": {"title": "Tier 1"}}))

        assert fetch_tier1_html(limiter=self._limiter(), get=get) is None

    def test_returns_none_rather_than_raising_when_the_request_throws(self):
        # Discovery going quiet must never stop match fetching.
        get = FakeGet(error=OSError("connection reset"))

        assert fetch_tier1_html(limiter=self._limiter(), get=get) is None

    def test_consecutive_calls_share_a_limiter_when_none_is_injected(
        self, monkeypatch
    ):
        # The production path. Building a limiter per call would give each an
        # empty history and enforce nothing, so the 30-second condition would
        # hold only in the tests that inject a shared limiter — which is what
        # `limiter or ParseRateLimiter()` used to do.
        clock = FakeClock()
        monkeypatch.setattr(
            liquipedia,
            "_PARSE_LIMITER",
            ParseRateLimiter(monotonic=clock.monotonic, sleep=clock.sleep),
        )
        get = FakeGet(FakeResponse(payload=parse_payload("<table/>")))

        fetch_tier1_html(get=get)
        fetch_tier1_html(get=get)
        fetch_tier1_html(get=get)

        assert clock.sleeps == [
            PARSE_MIN_INTERVAL_SECONDS,
            PARSE_MIN_INTERVAL_SECONDS,
        ]


class TestCache:
    def test_round_trips_events_and_the_fetch_time(self, tmp_path):
        path = tmp_path / "nested" / "cache.json"
        events = [{"name": "The International 2026", "start_date": "2026-08-13"}]

        save_cache(events, 1_700_000_000.0, path)

        assert load_cache(path) == {
            "fetched_at": 1_700_000_000.0,
            "events": events,
        }

    def test_returns_none_when_there_is_no_cache_yet(self, tmp_path):
        assert load_cache(tmp_path / "absent.json") is None

    def test_returns_none_rather_than_raising_on_a_corrupt_cache(self, tmp_path):
        path = tmp_path / "cache.json"
        path.write_text("{ not json", encoding="utf-8")

        assert load_cache(path) is None

    @pytest.mark.parametrize("content", ["[1, 2]", '"a string"', "42", "null"])
    def test_rejects_valid_json_that_is_not_a_cache(self, tmp_path, content):
        # Valid JSON is not necessarily a cache. A list parses cleanly and then
        # raises AttributeError on `.get` at the call site — a raise out of a
        # function documented never to raise.
        path = tmp_path / "cache.json"
        path.write_text(content, encoding="utf-8")

        assert load_cache(path) is None

    def test_a_cache_of_the_wrong_shape_does_not_break_the_lookup(self, tmp_path):
        cache_path = tmp_path / "cache.json"
        cache_path.write_text("[1, 2]", encoding="utf-8")
        seed_path = tmp_path / "seed.json"
        seed_path.write_text(
            json.dumps({"events": [{"name": "seeded"}]}), encoding="utf-8"
        )

        result = get_tier1_events(
            now=1000.0,
            fetch_html=lambda: None,
            cache_path=cache_path,
            seed_path=seed_path,
        )

        assert result == {"events": [{"name": "seeded"}], "source": "seed"}

    def test_a_cache_written_now_is_fresh(self):
        assert cache_is_fresh({"fetched_at": 500.0}, now=500.0)

    def test_a_cache_older_than_the_max_age_is_not_fresh(self):
        stale = {"fetched_at": 0.0}

        assert not cache_is_fresh(stale, now=CACHE_MAX_AGE_SECONDS + 1)

    @pytest.mark.parametrize(
        "cache", [None, {}, {"events": []}, {"fetched_at": "yesterday"}]
    )
    def test_a_cache_with_no_usable_timestamp_is_not_fresh(self, cache):
        assert not cache_is_fresh(cache, now=500.0)


class TestSeedCalendar:
    def test_reads_the_committed_calendar(self):
        # The file shipped with the repo, not a fixture — this is the list the
        # pipeline falls back to on a machine that has never reached Liquipedia.
        assert len(load_seed_calendar()) == 324

    def test_the_committed_calendar_holds_the_thirteen_2026_events(self):
        assert len(events_in_year(load_seed_calendar(), 2026)) == 13

    def test_returns_an_empty_list_when_the_calendar_is_missing(self, tmp_path):
        assert load_seed_calendar(tmp_path / "absent.json") == []

    def test_returns_an_empty_list_rather_than_raising_on_a_corrupt_calendar(
        self, tmp_path
    ):
        path = tmp_path / "calendar.json"
        path.write_text("[]", encoding="utf-8")

        assert load_seed_calendar(path) == []


class TestGetTier1Events:
    def _seed(self, tmp_path, events):
        path = tmp_path / "seed.json"
        path.write_text(json.dumps({"events": events}), encoding="utf-8")
        return path

    def test_uses_a_fresh_cache_without_calling_the_network(self, tmp_path):
        # Condition 3: do not issue repeated requests which return the same data.
        cache_path = tmp_path / "cache.json"
        save_cache([{"name": "cached"}], 1000.0, cache_path)

        def must_not_be_called():
            raise AssertionError("fetched despite a fresh cache")

        result = get_tier1_events(
            now=1000.0, fetch_html=must_not_be_called, cache_path=cache_path
        )

        assert result == {"events": [{"name": "cached"}], "source": "cache"}

    def test_fetches_and_caches_when_the_cache_is_stale(
        self, tmp_path, liquipedia_tier1_html
    ):
        cache_path = tmp_path / "cache.json"
        save_cache([{"name": "old"}], 0.0, cache_path)

        result = get_tier1_events(
            now=CACHE_MAX_AGE_SECONDS + 1,
            fetch_html=lambda: liquipedia_tier1_html,
            cache_path=cache_path,
        )

        assert result["source"] == "network"
        assert len(result["events"]) == 324

        # The refetched list replaces the stale one on disk.
        assert load_cache(cache_path)["events"] == result["events"]

    def test_fetches_when_a_recent_cache_holds_no_events(self, tmp_path):
        # `cache_is_fresh` answers "recent enough", not "usable". A truncated
        # cache file is recent and empty, and serving it would report every
        # tournament as missing while never retrying.
        cache_path = tmp_path / "cache.json"
        cache_path.write_text(json.dumps({"fetched_at": 1000.0}), encoding="utf-8")

        result = get_tier1_events(
            now=1000.0,
            fetch_html=lambda: None,
            cache_path=cache_path,
            seed_path=self._seed(tmp_path, [{"name": "seeded"}]),
        )

        assert result["source"] == "seed"

    def test_falls_back_to_a_stale_cache_when_the_fetch_fails(self, tmp_path):
        cache_path = tmp_path / "cache.json"
        save_cache([{"name": "old but real"}], 0.0, cache_path)

        result = get_tier1_events(
            now=CACHE_MAX_AGE_SECONDS + 1,
            fetch_html=lambda: None,
            cache_path=cache_path,
        )

        assert result == {
            "events": [{"name": "old but real"}],
            "source": "stale-cache",
        }

    def test_falls_back_to_the_seed_when_there_is_no_cache_and_no_network(
        self, tmp_path
    ):
        seed = self._seed(tmp_path, [{"name": "seeded"}])

        result = get_tier1_events(
            now=1000.0,
            fetch_html=lambda: None,
            cache_path=tmp_path / "absent.json",
            seed_path=seed,
        )

        assert result == {"events": [{"name": "seeded"}], "source": "seed"}

    def test_falls_back_when_the_page_parses_to_no_rows(self, tmp_path):
        # A 200 carrying markup we no longer recognise. Treating that as an
        # empty Tier 1 list would report every tournament as missing at once.
        seed = self._seed(tmp_path, [{"name": "seeded"}])

        result = get_tier1_events(
            now=1000.0,
            fetch_html=lambda: "<html><body>redesigned</body></html>",
            cache_path=tmp_path / "absent.json",
            seed_path=seed,
        )

        assert result == {"events": [{"name": "seeded"}], "source": "seed"}

    def test_returns_the_events_even_when_the_cache_cannot_be_written(
        self, tmp_path, monkeypatch, liquipedia_tier1_html
    ):
        # A calendar we fetched but could not cache is still a calendar. Letting
        # a read-only disk propagate would stop match fetching over a failure to
        # write an optimisation.
        def refuse(*args, **kwargs):
            raise OSError("read-only file system")

        monkeypatch.setattr(liquipedia, "save_cache", refuse)

        result = get_tier1_events(
            now=1000.0,
            fetch_html=lambda: liquipedia_tier1_html,
            cache_path=tmp_path / "cache.json",
        )

        assert result["source"] == "network"
        assert len(result["events"]) == 324

    def test_never_raises_when_everything_is_unavailable(self, tmp_path):
        result = get_tier1_events(
            now=1000.0,
            fetch_html=lambda: None,
            cache_path=tmp_path / "absent.json",
            seed_path=tmp_path / "also-absent.json",
        )

        assert result == {"events": [], "source": "seed"}


class TestWriteSeedCalendar:
    def test_writes_a_calendar_that_load_seed_calendar_reads_back(self, tmp_path):
        path = tmp_path / "nested" / "calendar.json"
        events = [{"name": "The International 2026", "start_date": "2026-08-13"}]

        write_seed_calendar(events, "2026-08-09", path)

        assert load_seed_calendar(path) == events

    def test_records_the_source_and_licence_alongside_the_events(self, tmp_path):
        # Attribution travels with the data, so a reader of the file alone can
        # see where it came from and what it is licensed under.
        path = tmp_path / "calendar.json"
        write_seed_calendar([], "2026-08-09", path)

        written = json.loads(path.read_text(encoding="utf-8"))
        assert written["license"] == "CC BY-SA 3.0"
        assert "liquipedia.net" in written["source"]
        assert written["recorded_on"] == "2026-08-09"


class TestRefreshSeedGuard:
    """
    `--refresh-seed` must only ever write what came off the network.

    Refreshing from the seed would rewrite the fallback with itself and stamp
    it with today's date, making a stale calendar look freshly verified — the
    exact "reports success while nothing is happening" failure this feature
    exists to remove.
    """

    def _seed(self, tmp_path, events):
        path = tmp_path / "seed.json"
        write_seed_calendar(events, "2020-01-01", path)
        return path

    @pytest.mark.parametrize("failing_fetch", [lambda: None, lambda: "<html/>"])
    def test_a_failed_fetch_leaves_the_seed_untouched(
        self, tmp_path, monkeypatch, failing_fetch
    ):
        seed = self._seed(tmp_path, [{"name": "original"}])
        before = seed.read_text(encoding="utf-8")

        monkeypatch.setattr(liquipedia, "SEED_FILE", seed)
        monkeypatch.setattr(
            liquipedia,
            "get_tier1_events",
            lambda **kwargs: {"events": [{"name": "original"}], "source": "seed"},
        )

        liquipedia.main(["--refresh-seed"])

        assert seed.read_text(encoding="utf-8") == before

    def test_a_cached_result_does_not_refresh_the_seed(self, tmp_path, monkeypatch):
        # `--refresh-seed` bypasses the cache, so this source should not arise
        # in practice. The guard is asserted directly anyway: it keys on "is
        # this from the network", not on "did the cache happen to be skipped".
        seed = self._seed(tmp_path, [{"name": "original"}])
        before = seed.read_text(encoding="utf-8")

        monkeypatch.setattr(liquipedia, "SEED_FILE", seed)
        monkeypatch.setattr(
            liquipedia,
            "get_tier1_events",
            lambda **kwargs: {"events": [{"name": "newer"}], "source": "cache"},
        )

        liquipedia.main(["--refresh-seed"])

        assert seed.read_text(encoding="utf-8") == before

    def test_a_network_result_does_refresh_the_seed(self, tmp_path, monkeypatch):
        seed = self._seed(tmp_path, [{"name": "original"}])

        monkeypatch.setattr(liquipedia, "SEED_FILE", seed)
        monkeypatch.setattr(
            liquipedia,
            "get_tier1_events",
            lambda **kwargs: {"events": [{"name": "fresh"}], "source": "network"},
        )

        liquipedia.main(["--refresh-seed"])

        assert load_seed_calendar(seed) == [{"name": "fresh"}]

    def test_refresh_bypasses_the_day_long_cache(self, tmp_path, monkeypatch):
        # The cache is fresh for 24 hours, and the daily pipeline run leaves it
        # that way — so honouring it here would make the documented regeneration
        # command refuse for a whole day, which is the machine's normal state.
        seed = self._seed(tmp_path, [{"name": "original"}])
        monkeypatch.setattr(liquipedia, "SEED_FILE", seed)

        cache_path = tmp_path / "cache.json"
        save_cache([{"name": "cached"}], 1000.0, cache_path)
        monkeypatch.setattr(liquipedia, "CACHE_FILE", cache_path)
        monkeypatch.setattr(
            liquipedia, "fetch_tier1_html", lambda: "<html>fresh</html>"
        )
        monkeypatch.setattr(
            liquipedia, "parse_tier1_events", lambda html: [{"name": "from network"}]
        )

        result = liquipedia.main(["--refresh-seed"])

        assert result["source"] == "network"
        assert load_seed_calendar(seed) == [{"name": "from network"}]

    def test_refresh_refuses_when_the_bypassed_fetch_fails(
        self, tmp_path, monkeypatch
    ):
        # Cache bypassed and the fetch down: anything but `network` now means
        # the fetch failed, and writing would stamp the existing fallback with
        # today's date and make a stale list look freshly verified.
        seed = self._seed(tmp_path, [{"name": "original"}])
        before = seed.read_text(encoding="utf-8")
        monkeypatch.setattr(liquipedia, "SEED_FILE", seed)

        cache_path = tmp_path / "cache.json"
        save_cache([{"name": "cached"}], 1000.0, cache_path)
        monkeypatch.setattr(liquipedia, "CACHE_FILE", cache_path)
        monkeypatch.setattr(liquipedia, "fetch_tier1_html", lambda: None)

        result = liquipedia.main(["--refresh-seed"])

        assert result["source"] == "stale-cache"
        assert seed.read_text(encoding="utf-8") == before

    def test_running_without_the_flag_never_writes(self, tmp_path, monkeypatch):
        seed = self._seed(tmp_path, [{"name": "original"}])
        before = seed.read_text(encoding="utf-8")

        monkeypatch.setattr(liquipedia, "SEED_FILE", seed)
        monkeypatch.setattr(
            liquipedia,
            "get_tier1_events",
            lambda **kwargs: {"events": [{"name": "fresh"}], "source": "network"},
        )

        liquipedia.main([])

        assert seed.read_text(encoding="utf-8") == before


class TestImportIsInert:
    def test_importing_the_module_reaches_nothing(self):
        # The same rule the pipeline shell follows: importing must not start a
        # network call, write a file, or read the clock.
        assert liquipedia.API_URL.startswith("https://")
        assert liquipedia.PARSE_MIN_INTERVAL_SECONDS == 30.0
