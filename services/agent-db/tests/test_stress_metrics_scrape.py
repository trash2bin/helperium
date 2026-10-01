"""Server ``/metrics`` slice collection (§10).

This is the harness's only *independent* statement about a run: everything else
it reports is recomputed from its own records, which checks arithmetic rather
than corroborating it. So what is pinned here is the honesty of the collection -
a missed scrape is a named gap and never a zero, a 401 says so, and a dead
endpoint costs the run nothing - plus a parser that survives the exposition
shapes the real services emit (label values containing commas and quotes,
histogram buckets, series the harness does not want).

No sockets and no sleeping: the connection is injected, so every status code and
failure mode is an input a test can produce.
"""

from __future__ import annotations

import pytest

from agent_db.stress.metrics_scrape import (
    DEFAULT_KEEP_PREFIXES,
    MetricsCollector,
    MetricsTarget,
    ScrapeSlice,
    parse_exposition,
    scrape_once,
    targets_from,
)

GATEWAY_EXPOSITION = """# HELP mcp_tool_calls_total Total MCP tool calls.
# TYPE mcp_tool_calls_total counter
mcp_tool_calls_total{status="ok",tenant="g1",tool="db_map"} 3602
mcp_tool_calls_total{status="ok",tenant="g1",tool="db_get"} 3600
mcp_sessions_active{tenant_scope="g1"} 21
mcp_rate_limit_hits_total{tenant="g1"} 0
go_goroutines 42
"""


class _Response:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body


class _Connection:
    """A scripted HTTP connection: records what was asked, answers as told."""

    def __init__(
        self,
        response: _Response | None = None,
        *,
        raises: Exception | None = None,
    ) -> None:
        self.response = response
        self.raises = raises
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.closed = False

    def request(self, method: str, path: str, headers: dict[str, str]) -> None:
        if self.raises is not None:
            raise self.raises
        self.requests.append((method, path, dict(headers)))

    def getresponse(self) -> _Response:
        assert self.response is not None
        return self.response

    def close(self) -> None:
        self.closed = True


def _opener(connection: _Connection):
    def factory(host: str, port: int, timeout: float):
        connection.host = host
        connection.port = port
        connection.timeout = timeout
        return connection

    return factory


class TestExpositionParsing:
    def test_series_labels_and_values_are_read(self):
        samples = parse_exposition(GATEWAY_EXPOSITION)
        by_tool = {
            sample.labels["tool"]: sample.value
            for sample in samples
            if sample.name == "mcp_tool_calls_total"
        }
        assert by_tool == {"db_map": 3602.0, "db_get": 3600.0}

    def test_comments_and_unwanted_families_are_dropped(self):
        samples = parse_exposition(GATEWAY_EXPOSITION)
        names = {sample.name for sample in samples}
        # Go runtime series belong to host/, and keeping the whole exposition
        # per scrape would grow the artefact without making a claim checkable.
        assert "go_goroutines" not in names
        assert not any(sample.name.startswith("#") for sample in samples)

    def test_a_comma_inside_a_label_value_does_not_split_the_series(self):
        # Tenant ids and error labels really do contain commas, and splitting on
        # them would silently produce a wrong label set rather than an error.
        (sample,) = parse_exposition(
            'mcp_rate_limit_hits_total{tenant="a,b",status="ok"} 7'
        )
        assert sample.labels == {"tenant": "a,b", "status": "ok"}
        assert sample.value == 7.0

    def test_an_escaped_quote_inside_a_label_value_is_unescaped(self):
        (sample,) = parse_exposition(
            'mcp_tool_calls_total{tool="db_map",note="said \\"no\\""} 1'
        )
        assert sample.labels["note"] == 'said "no"'

    def test_histogram_buckets_are_kept_as_they_are(self):
        # Kept, not turned into a quantile: the first bucket edge is 1 ms and on
        # a loopback stand nearly everything lands under it, so an interpolated
        # quantile would look like a measurement and not be one.
        samples = parse_exposition(
            'data_request_duration_ms_bucket{entity="s",operation="GET",le="1"} 28840\n'
            'data_request_duration_ms_bucket{entity="s",operation="GET",le="+Inf"} 28897\n'
        )
        assert [sample.labels["le"] for sample in samples] == ["1", "+Inf"]
        assert [sample.value for sample in samples] == [28840.0, 28897.0]

    def test_a_malformed_line_is_skipped_rather_than_raised(self):
        # A run must not lose its ladder to one bad line in an exposition.
        samples = parse_exposition(
            "mcp_sessions_active{tenant_scope=broken 1\n"
            "mcp_tool_calls_total not-a-number\n"
            'mcp_sessions_active{tenant_scope="g1"} 3\n'
        )
        assert [(s.name, s.value) for s in samples] == [("mcp_sessions_active", 3.0)]

    def test_an_unlabelled_series_is_read(self):
        (sample,) = parse_exposition("process_cpu_seconds_total 12.34")
        assert sample.labels == {}
        assert sample.value == pytest.approx(12.34)


class TestOneScrape:
    def test_a_successful_scrape_carries_the_series_and_no_gap(self):
        connection = _Connection(_Response(200, GATEWAY_EXPOSITION))
        target = MetricsTarget("mcp-gateway", "http://127.0.0.1:8083/metrics")
        captured = scrape_once(target, opener=_opener(connection))

        assert captured.gap is None
        assert captured.samples
        assert "series" in captured.as_dict()
        assert connection.closed is True

    def test_the_credential_is_sent_because_metrics_is_fail_closed(self):
        connection = _Connection(_Response(200, GATEWAY_EXPOSITION))
        target = MetricsTarget(
            "data-service", "http://127.0.0.1:8084/metrics", bearer="t0ken"
        )
        scrape_once(target, opener=_opener(connection))

        (method, path, headers) = connection.requests[0]
        assert (method, path) == ("GET", "/metrics")
        assert headers["Authorization"] == "Bearer t0ken"

    def test_a_401_is_recorded_as_a_gap_naming_the_status(self):
        # /metrics is behind the same bearer as /admin/* (pentest M1), so a 401
        # is a stand configuration fact the reader wants, not noise.
        connection = _Connection(_Response(401, '{"error":"unauthorized"}'))
        captured = scrape_once(
            MetricsTarget("data-service", "http://127.0.0.1:8084/metrics"),
            opener=_opener(connection),
        )
        assert captured.gap is not None
        assert "401" in captured.gap
        assert captured.samples == []

    def test_a_refused_connection_is_a_gap_not_a_zero(self):
        """The distinction the whole module exists for.

        A zero means "the server answered and the counter was zero". Writing one
        for a connection that was refused would turn a collection failure into
        evidence that the service was idle.
        """
        connection = _Connection(raises=ConnectionRefusedError("nope"))
        captured = scrape_once(
            MetricsTarget("mcp-gateway", "http://127.0.0.1:8083/metrics"),
            opener=_opener(connection),
        )
        assert captured.samples == []
        assert captured.gap is not None
        assert "ConnectionRefusedError" in captured.gap

    def test_a_200_with_nothing_expected_in_it_is_still_a_gap(self):
        # Answering 200 with an exposition that carries none of the families is
        # not an observation of zero traffic - it is the wrong endpoint.
        captured = scrape_once(
            MetricsTarget("mcp-gateway", "http://127.0.0.1:8083/metrics"),
            opener=_opener(_Connection(_Response(200, "go_goroutines 3\n"))),
        )
        assert captured.gap is not None
        assert "none of the expected series" in captured.gap

    def test_a_non_http_url_is_refused_without_opening_a_socket(self):
        captured = scrape_once(
            MetricsTarget("mcp-gateway", "ftp://127.0.0.1/metrics"),
        )
        assert captured.gap is not None
        assert "not http" in captured.gap

    def test_a_slice_never_carries_both_samples_and_a_gap(self):
        for captured in (
            scrape_once(
                MetricsTarget("s", "http://127.0.0.1:1/metrics"),
                opener=_opener(_Connection(_Response(200, GATEWAY_EXPOSITION))),
            ),
            scrape_once(
                MetricsTarget("s", "http://127.0.0.1:1/metrics"),
                opener=_opener(_Connection(_Response(500, "boom"))),
            ),
        ):
            payload = captured.as_dict()
            assert ("series" in payload) != ("gap" in payload)


class TestCollector:
    def _collector(self, slices: list[ScrapeSlice], **kwargs) -> MetricsCollector:
        remaining = list(slices)

        def scraper(target: MetricsTarget) -> ScrapeSlice:
            return remaining.pop(0) if remaining else slices[-1]

        return MetricsCollector(
            [MetricsTarget("mcp-gateway", "http://127.0.0.1:8083/metrics")],
            scraper=scraper,
            **kwargs,
        )

    def test_sampling_accumulates_slices_per_service(self):
        collector = self._collector(
            [
                ScrapeSlice(wall_ms=1.0, monotonic_ms=1.0, gap="first"),
                ScrapeSlice(wall_ms=2.0, monotonic_ms=2.0, gap="second"),
            ]
        )
        collector.sample_now()
        collector.sample_now()

        payload = collector.payloads()["mcp-gateway"]
        assert payload["scrapes"] == 2
        assert payload["gap_count"] == 2
        assert payload["gap_reasons"] == ["first", "second"]

    def test_the_payload_counts_gaps_so_a_reader_can_judge_the_window(self):
        from agent_db.stress.metrics_scrape import MetricSample

        good = ScrapeSlice(
            wall_ms=1.0,
            monotonic_ms=1.0,
            samples=[MetricSample("mcp_sessions_active", {"tenant_scope": "g1"}, 4.0)],
        )
        collector = self._collector([good, ScrapeSlice(2.0, 2.0, gap="timeout"), good])
        for _ in range(3):
            collector.sample_now()

        payload = collector.payloads()["mcp-gateway"]
        assert (payload["scrapes"], payload["gap_count"]) == (3, 1)
        assert payload["gap_reasons"] == ["timeout"]
        # The url and whether a credential was used travel with the numbers: a
        # cross-check nobody can re-point at the same endpoint is not repeatable.
        assert payload["url"] == "http://127.0.0.1:8083/metrics"
        assert payload["authenticated"] is False
        assert payload["kept_prefixes"] == list(DEFAULT_KEEP_PREFIXES)

    def test_start_takes_a_slice_before_the_first_tick(self):
        # Counters are cumulative since process start, so without a pre-run
        # sample the first stage's delta is indistinguishable from whatever the
        # service did earlier - a preflight, for one.
        collector = self._collector(
            [ScrapeSlice(1.0, 1.0, gap="pre-run")], interval_s=3600.0
        )
        collector.start()
        try:
            assert collector.payloads()["mcp-gateway"]["scrapes"] == 1
        finally:
            collector.stop()

    def test_stop_takes_a_final_slice_after_the_last_tick(self):
        collector = self._collector(
            [ScrapeSlice(1.0, 1.0, gap="pre"), ScrapeSlice(2.0, 2.0, gap="post")],
            interval_s=3600.0,
        )
        collector.start()
        collector.stop()

        payload = collector.payloads()["mcp-gateway"]
        # Bracketed on both sides, so a stage delta is computable after the fact.
        assert payload["scrapes"] == 2
        assert payload["gap_reasons"] == ["post", "pre"]

    def test_stopping_does_not_wait_out_the_interval(self):
        # A long interval must not keep the process alive past its own report.
        collector = self._collector(
            [ScrapeSlice(1.0, 1.0, gap="pre")], interval_s=600.0
        )
        collector.start()
        collector.stop()
        assert collector._thread is None

    def test_a_zero_interval_is_refused(self):
        with pytest.raises(ValueError, match="interval must be positive"):
            MetricsCollector(
                [MetricsTarget("s", "http://127.0.0.1:1/metrics")], interval_s=0
            )

    def test_no_targets_is_not_an_error_and_starts_nothing(self):
        collector = MetricsCollector([])
        collector.start()
        collector.stop()
        assert collector.payloads() == {}


class TestTargetDerivation:
    def test_the_gateway_metrics_url_comes_from_the_mcp_url(self):
        (target,) = targets_from(
            {"mcp_url": "http://127.0.0.1:18083/mcp"}, api_key="k", admin_token=""
        )
        assert (target.service, target.url) == (
            "mcp-gateway",
            "http://127.0.0.1:18083/metrics",
        )
        assert target.api_key == "k"

    def test_the_chat_url_yields_the_api_service_with_its_bearer(self):
        (target,) = targets_from(
            {"chat_url": "http://127.0.0.1:8081"}, api_key="", admin_token="adm"
        )
        assert (target.service, target.url) == (
            "api-service",
            "http://127.0.0.1:8081/metrics",
        )
        assert target.bearer == "adm"

    def test_data_service_is_only_known_when_declared(self):
        # The harness never talks to data-service directly, so deriving its
        # address would be a guess printed as provenance.
        derived = targets_from(
            {"mcp_url": "http://127.0.0.1:18083/mcp"}, api_key="", admin_token=""
        )
        assert [t.service for t in derived] == ["mcp-gateway"]

        with_extra = targets_from(
            {"mcp_url": "http://127.0.0.1:18083/mcp"},
            api_key="",
            admin_token="adm",
            extra=["data-service=http://127.0.0.1:18084"],
        )
        assert [t.service for t in with_extra] == ["mcp-gateway", "data-service"]
        assert with_extra[1].url == "http://127.0.0.1:18084/metrics"
        assert with_extra[1].bearer == "adm"

    def test_an_explicit_target_corrects_a_derived_one(self):
        targets = targets_from(
            {"mcp_url": "http://127.0.0.1:18083/mcp"},
            api_key="k",
            admin_token="adm",
            extra=["mcp-gateway=http://10.0.0.5:9000/metrics"],
        )
        assert len(targets) == 1
        assert targets[0].url == "http://10.0.0.5:9000/metrics"

    def test_an_explicit_metrics_path_is_kept(self):
        (target,) = targets_from(
            {}, api_key="", admin_token="", extra=["x=http://h:1/custom/metrics"]
        )
        assert target.url == "http://h:1/custom/metrics"

    @pytest.mark.parametrize("bad", ["data-service", "=http://h:1", "data-service="])
    def test_a_malformed_target_is_a_refusal_naming_the_form(self, bad):
        with pytest.raises(ValueError, match="SERVICE=URL"):
            targets_from({}, api_key="", admin_token="", extra=[bad])

    def test_a_url_without_a_scheme_is_refused(self):
        with pytest.raises(ValueError, match="cannot derive"):
            targets_from({}, api_key="", admin_token="", extra=["x=127.0.0.1:8084"])
