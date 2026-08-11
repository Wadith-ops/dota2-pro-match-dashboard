"""
What the fetcher does when a call does not come back with a 200.

Two levels, and the split is the same one the rest of the project uses. *What
to retry, for how long, and how many times* is arithmetic over a status code —
`core.retry_pause`, tested here against plain data. *Asking again, sleeping,
and giving up* is the shell — `opendota_pipeline.fetch_url`, tested here
against a stubbed transport.

Nothing touches the network, and nothing sleeps: `requests.get` and
`time.sleep` are both replaced, so the 30/60/120 second schedule is asserted
on rather than waited out.
"""
import pytest
import requests

import core
import opendota_pipeline as pipeline


class TestWhatIsWorthAskingAgain:
    """
    `core.retry_pause` is the whole policy. A status either says "ask again" or
    it does not, and the cost of getting that wrong runs both ways: a retried
    404 spends a minute on a match that will never exist, and an unretried
    timeout throws away a tournament because a packet went missing.
    """

    def test_a_missing_page_is_never_retried(self):
        # A 404 will still be a 404 in two minutes.
        assert core.retry_pause(404, 0) is None

    def test_a_rate_limit_waits_long_enough_for_the_window_to_move(self):
        # The free tier's window is a minute wide, so a short retry is refused
        # again. This is the schedule the resolver's walk needs.
        assert core.retry_pause(429, 0) == core.RATE_LIMIT_BACKOFFS[0]
        assert core.retry_pause(429, 0) >= 30.0

    def test_a_request_that_never_got_a_status_is_transient(self):
        # None is a timeout, a dropped connection, a DNS failure: there is no
        # response to read a verdict out of, and the next call usually works.
        assert core.retry_pause(None, 0) == core.TRANSIENT_BACKOFFS[0]

    def test_a_server_error_is_transient(self):
        for status in (500, 502, 503, 504):
            assert core.retry_pause(status, 0) is not None

    def test_a_transient_failure_waits_seconds_not_minutes(self):
        # Nothing has been refused here, so there is no window to sit out. The
        # rate limit schedule would spend three and a half minutes per call on
        # a network that is simply down — and the pipeline makes thousands.
        assert core.retry_pause(None, 0) < core.retry_pause(429, 0)

    def test_the_pauses_lengthen(self):
        assert core.retry_pause(429, 1) > core.retry_pause(429, 0)
        assert core.retry_pause(None, 1) > core.retry_pause(None, 0)

    def test_the_retries_are_bounded(self):
        # Bounded is the point: an unattended run must fail a call and move on
        # rather than hold the whole schedule open against a dead endpoint.
        assert core.retry_pause(429, len(core.RATE_LIMIT_BACKOFFS)) is None
        assert core.retry_pause(None, len(core.TRANSIENT_BACKOFFS)) is None

    def test_a_success_is_not_something_to_ask_again(self):
        assert core.retry_pause(200, 0) is None


@pytest.fixture
def transport(monkeypatch):
    """
    Replaces `requests.get` and `time.sleep`.

    Assign `transport.answers` to the sequence of replies the fetcher should
    see: an int is a status code, an Exception instance is raised as if the
    request never completed. Every call's keyword arguments land on
    `transport.calls`, and every sleep on `transport.slept`.
    """
    class Response:
        def __init__(self, status_code):
            self.status_code = status_code

        def json(self):
            return {"ok": True}

    class Stub:
        def __init__(self):
            self.answers = []
            self.calls   = []
            self.slept   = []

        def get(self, url, **kwargs):
            self.calls.append(kwargs)
            answer = self.answers.pop(0) if self.answers else 200

            if isinstance(answer, Exception):
                raise answer

            return Response(answer)

    stub = Stub()
    monkeypatch.setattr(requests, "get", stub.get)
    monkeypatch.setattr(pipeline.time, "sleep", stub.slept.append)
    return stub


class TestEveryCallHasADeadline:
    """
    A request with no timeout waits forever by default. On a workstation that
    is a hung terminal somebody notices; on a six-hourly runner it is a job
    that never ends and a schedule that never runs again.
    """

    def test_the_fetcher_passes_an_explicit_timeout(self, transport):
        pipeline.fetch_url("/x")

        assert transport.calls[0]["timeout"] == pipeline.REQUEST_TIMEOUT_SECONDS

    def test_the_timeout_is_a_real_number_of_seconds(self):
        assert pipeline.REQUEST_TIMEOUT_SECONDS


class TestSurvivingATransientFailure:
    def test_a_dropped_connection_is_retried_and_then_succeeds(self, transport):
        transport.answers = [requests.ConnectionError("connection reset"), 200]

        assert pipeline.fetch_url("/x") == {"ok": True}

    def test_a_timeout_is_retried_and_then_succeeds(self, transport):
        transport.answers = [requests.Timeout("read timed out"), 200]

        assert pipeline.fetch_url("/x") == {"ok": True}

    def test_a_server_error_is_retried_and_then_succeeds(self, transport):
        transport.answers = [503, 200]

        assert pipeline.fetch_url("/x") == {"ok": True}

    def test_the_retry_waits_first(self, transport):
        transport.answers = [requests.Timeout("read timed out"), 200]

        pipeline.fetch_url("/x")

        assert core.TRANSIENT_BACKOFFS[0] in transport.slept

    def test_retries_run_out_and_the_call_fails(self, transport):
        transport.answers = [requests.Timeout("read timed out")] * 20

        assert pipeline.fetch_url("/x") is None
        # One first attempt, then one per pause in the schedule. Bounded, so a
        # league that cannot be reached costs seconds rather than the run.
        assert len(transport.calls) == len(core.TRANSIENT_BACKOFFS) + 1

    def test_a_permanent_failure_is_asked_once(self, transport):
        transport.answers = [404]

        assert pipeline.fetch_url("/x") is None
        assert len(transport.calls) == 1

    def test_a_body_that_is_not_json_fails_rather_than_raising(
        self, transport, monkeypatch
    ):
        # A 200 carrying HTML — a proxy's error page, most often. It is not a
        # transport failure, so it is not retried; it is also not a payload,
        # so it must not propagate out of a function documented to answer None.
        class Broken:
            status_code = 200

            def json(self):
                raise ValueError("Expecting value: line 1 column 1")

        monkeypatch.setattr(requests, "get", lambda url, **kwargs: Broken())

        assert pipeline.fetch_url("/x") is None

    def test_the_policy_is_injectable_so_a_caller_can_stop_sooner(self, transport):
        transport.answers = [500, 500, 200]

        assert pipeline.fetch_url(
            "/x", retry_policy=lambda status, attempt: None
        ) is None

    def test_an_unexpected_error_fails_the_call_rather_than_ending_the_run(
        self, monkeypatch
    ):
        # `requests` mostly wraps what it raises, but not invariably, and one
        # league's fetch raising is one league's fetch ending the whole run.
        # Not retried, though: an error nobody anticipated is no evidence that
        # asking again would help.
        def explode(url, **kwargs):
            raise RuntimeError("something nobody anticipated")

        monkeypatch.setattr(requests, "get", explode)

        assert pipeline.fetch_url("/x") is None

    def test_a_call_that_fails_several_ways_still_ends(self, transport):
        # One four-attempt budget per call, shared across the kinds. The
        # property worth guaranteeing is that a single call cannot hold the run
        # open — not that each kind of failure gets its own allowance.
        transport.answers = [429, 503, requests.Timeout("gone"), 429, 200]

        assert pipeline.fetch_url("/x") is None
        assert len(transport.calls) == 4


class TestBeingRateLimited:
    """
    A 429 is the one status that says "ask again". Treating it like a 404 throws
    away whatever the call was fetching — which is what the resolver's first
    real run did, twelve times in a row, after its pro match walk used up the
    minute's allowance.
    """

    def test_a_rate_limited_call_is_retried_and_succeeds(self, transport):
        transport.answers = [429, 200]

        assert pipeline.fetch_url("/x") == {"ok": True}

    def test_the_backoffs_lengthen_and_then_give_up(self, transport):
        transport.answers = [429] * 20

        assert pipeline.fetch_url("/x") is None
        assert [s for s in transport.slept
                if s in core.RATE_LIMIT_BACKOFFS] == list(core.RATE_LIMIT_BACKOFFS)

    def test_the_delay_between_calls_sits_inside_sixty_a_minute_not_on_it(self):
        # Exactly one second is the limit, not under it: the sleep starts after
        # the response, so a run of fast responses puts slightly more than sixty
        # calls inside a rolling minute.
        assert pipeline.DELAY_SECONDS > 1.0


class TestFetchingThePatchConstants:
    """
    The patch map is an HTTP call like any other, and for two months it was the
    one that went out with no timeout, no retry and its own error handling. It
    goes through `fetch_url` now, so it inherits all three.
    """

    def test_a_successful_fetch_maps_ids_to_names(self, transport, monkeypatch):
        monkeypatch.setattr(
            pipeline, "fetch_url",
            lambda url, **kwargs: [{"id": 58, "name": "7.39"},
                                   {"id": 59, "name": "7.40"}],
        )

        assert pipeline.get_patch_map() == {58: "7.39", 59: "7.40"}

    def test_a_failed_fetch_leaves_the_labels_unknown_rather_than_raising(
        self, monkeypatch
    ):
        # Every row still gets a patch label — `core.flatten_match` writes
        # "Unknown" when the map has no entry. A run without patch names is
        # worse than one with them and far better than no run at all.
        monkeypatch.setattr(pipeline, "fetch_url", lambda url, **kwargs: None)

        assert pipeline.get_patch_map() == {}

    def test_a_payload_that_is_not_a_list_is_a_failure(self, monkeypatch):
        # OpenDota answers 200 with an error object often enough that the
        # league list already guards against it.
        monkeypatch.setattr(pipeline, "fetch_url",
                            lambda url, **kwargs: {"error": "rate limit exceeded"})

        assert pipeline.get_patch_map() == {}

    def test_an_empty_list_is_a_failure_not_a_patchless_game(self, monkeypatch):
        # The same rule the league list follows, and the opposite of the one
        # the match loop follows: a league legitimately has no matches before
        # it starts, and there is no such thing as a Dota patch list with
        # nothing in it.
        monkeypatch.setattr(pipeline, "fetch_url", lambda url, **kwargs: [])

        assert pipeline.get_patch_map() == {}

    def test_a_constant_with_no_name_is_skipped_and_said_out_loud(
        self, monkeypatch, capsys
    ):
        # A partial map labels real patches "Unknown", which reads on the
        # dashboard as a data problem rather than as the fetch that caused it.
        monkeypatch.setattr(
            pipeline, "fetch_url",
            lambda url, **kwargs: [{"id": 58, "name": "7.39"}, {"id": 59}],
        )

        assert pipeline.get_patch_map() == {58: "7.39"}
        assert "1 patch constant(s) had no id or name" in capsys.readouterr().out
