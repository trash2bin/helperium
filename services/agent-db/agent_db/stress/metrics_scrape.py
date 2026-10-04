"""Server-side ``/metrics`` slices, taken over the run (doc/stress/README.md §10).

Why this exists: without it every number in a report is self-checked. The
harness measures a turn from the outside and then recomputes its own
percentiles from its own raw records - arithmetic, not corroboration. A
counter read from the service says how many calls the *server* believes it
answered, which is the only independent statement available in phase 1.

What is collected, and what deliberately is not:

- **Counters and gauges, as slices over time.** A slice is a timestamped sample
  of the whole exposition, so a stage can be bracketed by the samples around it
  and the delta over a stage is computable after the fact.
- **Not percentiles.** ``data_request_duration_ms`` starts its buckets at 1 ms
  (``helperium-go/pkg/metrics/metrics.go``), and on a loopback stand nearly every
  request lands in the first bucket, so ``histogram_quantile`` would interpolate
  inside it and return a number that looks like a measurement and is not. The
  raw bucket counts are kept; deriving a quantile from them is the reader's
  decision, made with the bucket edges in view.

A missed scrape is recorded as a gap with its reason, never as a zero (§10). A
zero is a measurement meaning "the server answered and the counter was zero",
and silently writing one for a refused connection would turn a collection
failure into evidence of idleness. Collection never fails the run: a stand
without a reachable ``/metrics`` still produces a valid ladder, with the gap
named in the artefact.

Authentication is required, not optional: ``/metrics`` is behind the same bearer
as ``/admin/*`` on data-service (pentest M1), behind the API control-plane bearer
(``API_BEARER_TOKEN``) on api-service — its ``private_router`` guards ``/metrics``
and ``/admin/*`` with one dependency, so ``ADMIN_TOKEN`` gets a 403 there — and
behind the API key on the gateway, so an unauthenticated scrape gets a 401 -
which is recorded as a gap with the status, because a 401 is a stand
configuration fact worth reading.

**The observer is part of what it observes.** data-service counts every request
in ``StructuredLoggingMiddleware`` (``internal/server/server.go``), and
``/metrics`` is a request, so each scrape increments
``data_requests_total{entity="all"}`` by one. Measured on a native stand
2026-09-29: 22 slices over a 40/80 rps ladder gave a delta of 14421 where the
load accounts for 14400 - the remaining 21 are the scrapes themselves (the last
slice's own increment lands after its body is serialised, so N slices contribute
N-1). The gateway's ``mcp_tool_calls_total`` counts tool calls rather than HTTP
requests and matched the harness exactly, 7200 against 7200. So a cross-check
must subtract ``scrapes - 1`` from request counters, and ``interval_s`` is kept
in the artefact for exactly that arithmetic.
"""

from __future__ import annotations

import http.client
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

#: How long a single scrape may take. A scrape that outlives its own interval
#: would queue behind itself and the samples would stop describing the stage
#: they are named after, so it is cut and recorded as a gap instead.
SCRAPE_TIMEOUT_S = 5.0

#: Prefixes worth keeping from an exposition. The endpoint also exports Go
#: runtime and process series; those are what ``host/`` is for, and keeping the
#: whole exposition per scrape would grow the artefact without making a single
#: claim checkable.
DEFAULT_KEEP_PREFIXES: tuple[str, ...] = (
    # gateway
    "mcp_tool_calls_total",
    "mcp_sessions_active",
    "mcp_rate_limit_hits_total",
    # data-service
    "data_requests_total",
    "data_request_duration_ms",
    "data_db_query_duration_ms",
    # api-service (L2+)
    "llm_calls_total",
    "llm_duration_ms",
    "llm_token_usage",
    "chat_messages_total",
    "mcp_lock_wait_seconds",
    "mcp_lock_timeouts_total",
    "mcp_tool_timeouts_total",
    "mcp_reconnects_total",
    "mcp_circuit_breaker_trips_total",
    "backlog_records_total",
    "backlog_errors_total",
    # every service
    "process_cpu_seconds_total",
    "process_resident_memory_bytes",
    "process_open_fds",
)


@dataclass(frozen=True)
class MetricsTarget:
    """One ``/metrics`` endpoint and the credential it needs."""

    service: str
    url: str
    #: Bearer token (data-service, api-service) - sent as ``Authorization``.
    bearer: str | None = None
    #: API key (gateway) - the gateway accepts the same value as a bearer.
    api_key: str | None = None

    def headers(self) -> dict[str, str]:
        token = self.bearer or self.api_key
        return {"Authorization": f"Bearer {token}"} if token else {}


@dataclass(frozen=True)
class MetricSample:
    """One series at one point in time."""

    name: str
    labels: dict[str, str]
    value: float


@dataclass
class ScrapeSlice:
    """One scrape: either samples, or a named gap. Never both, never neither."""

    wall_ms: float
    monotonic_ms: float
    samples: list[MetricSample] = field(default_factory=list)
    gap: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "wall_ms": round(self.wall_ms, 3),
            "monotonic_ms": round(self.monotonic_ms, 3),
        }
        if self.gap is not None:
            # No "series": {} next to a gap. An empty mapping reads as "nothing
            # was exported", which is a different claim from "we did not look".
            payload["gap"] = self.gap
            return payload
        payload["series"] = [
            {"name": sample.name, "labels": sample.labels, "value": sample.value}
            for sample in self.samples
        ]
        return payload


def parse_exposition(
    text: str, *, keep_prefixes: Sequence[str] = DEFAULT_KEEP_PREFIXES
) -> list[MetricSample]:
    """Parse the Prometheus text format, keeping the named families.

    Deliberately a small parser rather than a dependency: the harness must run
    on a stand with nothing installed, and the text format's grammar for the
    subset that matters here (``name{labels} value`` plus ``#`` comments) is
    fixed. Anything unparsable is skipped rather than raised - a malformed line
    in an exposition must not cost the run its ladder.
    """
    samples: list[MetricSample] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if keep_prefixes and not line.startswith(tuple(keep_prefixes)):
            continue
        head, _, value_text = line.rpartition(" ")
        if not head:
            continue
        try:
            value = float(value_text)
        except ValueError:
            continue
        name, labels = _split_labels(head)
        if name is None:
            continue
        samples.append(MetricSample(name=name, labels=labels, value=value))
    return samples


def _split_labels(head: str) -> tuple[str | None, dict[str, str]]:
    if "{" not in head:
        return head.strip() or None, {}
    name, _, rest = head.partition("{")
    body = rest.rstrip()
    if not body.endswith("}"):
        return None, {}
    labels: dict[str, str] = {}
    # Values may contain commas and escaped quotes, so the body is walked rather
    # than split: `tool="db_map",tenant="g1"` must not break on a comma inside a
    # value, which tenant names and error labels do contain.
    for key, value in _iter_label_pairs(body[:-1]):
        labels[key] = value
    return name.strip() or None, labels


def _iter_label_pairs(body: str) -> Iterable[tuple[str, str]]:
    index = 0
    length = len(body)
    while index < length:
        equals = body.find("=", index)
        if equals == -1:
            return
        key = body[index:equals].strip().lstrip(",").strip()
        if equals + 1 >= length or body[equals + 1] != '"':
            return
        cursor = equals + 2
        chars: list[str] = []
        while cursor < length:
            char = body[cursor]
            if char == "\\" and cursor + 1 < length:
                nxt = body[cursor + 1]
                chars.append({"n": "\n", "t": "\t"}.get(nxt, nxt))
                cursor += 2
                continue
            if char == '"':
                break
            chars.append(char)
            cursor += 1
        if key:
            yield key, "".join(chars)
        index = cursor + 1
        while index < length and body[index] in ", ":
            index += 1


def scrape_once(
    target: MetricsTarget,
    *,
    timeout_s: float = SCRAPE_TIMEOUT_S,
    keep_prefixes: Sequence[str] = DEFAULT_KEEP_PREFIXES,
    opener: Callable[..., Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> ScrapeSlice:
    """Take one slice. Any failure becomes a named gap, never an exception."""
    started = clock() * 1000.0
    wall = time.time() * 1000.0
    parts = urlsplit(target.url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ScrapeSlice(
            wall_ms=wall,
            monotonic_ms=started,
            gap=f"target url is not http(s): {target.url!r}",
        )
    factory = opener or (
        http.client.HTTPSConnection
        if parts.scheme == "https"
        else http.client.HTTPConnection
    )
    connection = None
    try:
        connection = factory(
            parts.hostname,
            parts.port or (443 if parts.scheme == "https" else 80),
            timeout=timeout_s,
        )
        path = parts.path or "/metrics"
        if parts.query:
            path = f"{path}?{parts.query}"
        connection.request("GET", path, headers=target.headers())
        response = connection.getresponse()
        body = response.read().decode("utf-8", errors="replace")
        status = response.status
        if status != 200:
            # A 401 here is a stand fact worth reading, not noise: /metrics is
            # fail-closed behind the same bearer as /admin/* (pentest M1), so an
            # unauthenticated scrape says the credential was not passed.
            return ScrapeSlice(
                wall_ms=wall,
                monotonic_ms=started,
                gap=f"HTTP {status} from {target.service} /metrics",
            )
        samples = parse_exposition(body, keep_prefixes=keep_prefixes)
        if not samples:
            return ScrapeSlice(
                wall_ms=wall,
                monotonic_ms=started,
                gap=(
                    f"{target.service} answered 200 but exported none of the "
                    "expected series"
                ),
            )
        return ScrapeSlice(wall_ms=wall, monotonic_ms=started, samples=samples)
    except Exception as exc:  # noqa: BLE001 - a gap is evidence, not a failure
        return ScrapeSlice(
            wall_ms=wall,
            monotonic_ms=started,
            gap=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:  # noqa: BLE001 - nothing left to report it to
                pass


class MetricsCollector:
    """Scrapes each target on an interval for the length of a run.

    Runs on its own daemon thread so a slow endpoint cannot delay a tick: the
    generator's schedule is the measurement, and a scrape is an observation of
    it. The thread is stopped with an event rather than a flag so that stopping
    is immediate rather than up to one interval late - a run that ends while the
    collector sleeps would otherwise keep the process alive past its own report.
    """

    def __init__(
        self,
        targets: Sequence[MetricsTarget],
        *,
        interval_s: float = 5.0,
        timeout_s: float = SCRAPE_TIMEOUT_S,
        keep_prefixes: Sequence[str] = DEFAULT_KEEP_PREFIXES,
        scraper: Callable[[MetricsTarget], ScrapeSlice] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if interval_s <= 0:
            raise ValueError("metrics scrape interval must be positive")
        self.targets = list(targets)
        self.interval_s = interval_s
        self.timeout_s = timeout_s
        self.keep_prefixes = tuple(keep_prefixes)
        self.clock = clock
        self._scraper = scraper or self._scrape_target
        self._slices: dict[str, list[ScrapeSlice]] = {
            target.service: [] for target in self.targets
        }
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _scrape_target(self, target: MetricsTarget) -> ScrapeSlice:
        return scrape_once(
            target,
            timeout_s=self.timeout_s,
            keep_prefixes=self.keep_prefixes,
            clock=self.clock,
        )

    def sample_now(self) -> None:
        """Take one slice from every target, in place."""
        for target in self.targets:
            captured = self._scraper(target)
            with self._lock:
                self._slices[target.service].append(captured)

    def start(self) -> None:
        if not self.targets or self._thread is not None:
            return
        # A slice before the first tick, so a stage's delta has a lower bound to
        # subtract: counters are cumulative since process start, and without a
        # pre-run sample the first stage's share is indistinguishable from
        # whatever the service did before the run (a preflight, for one).
        self.sample_now()
        self._thread = threading.Thread(
            target=self._loop, name="stress-metrics", daemon=True
        )
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            self.sample_now()

    def stop(self) -> None:
        """Stop scraping and take a final slice after the last tick."""
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=self.timeout_s + self.interval_s)
        self._thread = None
        self.sample_now()

    def slices(self) -> dict[str, list[ScrapeSlice]]:
        with self._lock:
            return {
                service: list(captured) for service, captured in self._slices.items()
            }

    def payloads(self) -> dict[str, dict[str, Any]]:
        """One artefact per service, in the shape ``server/<service>.json`` wants."""
        payloads: dict[str, dict[str, Any]] = {}
        for target in self.targets:
            captured = self.slices().get(target.service, [])
            gaps = [item.gap for item in captured if item.gap is not None]
            payloads[target.service] = {
                "service": target.service,
                "url": target.url,
                "interval_s": self.interval_s,
                "authenticated": bool(target.bearer or target.api_key),
                "scrapes": len(captured),
                # Counted, not inferred from the list: a reader checking whether a
                # cross-check is trustworthy needs to know how much of the window
                # was actually observed.
                "gap_count": len(gaps),
                "gap_reasons": sorted(set(gaps)),
                "kept_prefixes": list(self.keep_prefixes),
                "slices": [item.as_dict() for item in captured],
            }
        return payloads


def targets_from(
    endpoints: Mapping[str, Any],
    *,
    api_key: str | None,
    admin_token: str | None,
    api_bearer: str | None = None,
    extra: Sequence[str] = (),
) -> list[MetricsTarget]:
    """Derive ``/metrics`` targets from the endpoints the run already talks to.

    The gateway's ``/mcp`` and the chat base URL are known, and ``/metrics`` sits
    at the root of the same origin, so the operator does not have to restate
    them. data-service is *not* derived: the harness never talks to it directly,
    so its address is only knowable if declared (``--metrics-target``).
    """
    targets: list[MetricsTarget] = []
    mcp_url = endpoints.get("mcp_url")
    if isinstance(mcp_url, str) and mcp_url:
        targets.append(
            MetricsTarget(
                service="mcp-gateway",
                url=_metrics_url(mcp_url),
                api_key=api_key or None,
            )
        )
    chat_url = endpoints.get("chat_url")
    if isinstance(chat_url, str) and chat_url:
        # /metrics on api-service sits behind require_api_bearer on the same
        # private_router as /admin/* (server/app.py), so it answers to the API
        # control-plane bearer (API_BEARER_TOKEN) — NOT to ADMIN_TOKEN, which
        # belongs to data-service. Sending the admin token here got a 403 for
        # every scrape of run 2026-10-03 (132 gaps: no server-side CPU, no
        # mcp_lock_wait in the report). Fallback keeps the old behaviour when
        # the operator has not declared an API bearer (rollback by omission).
        targets.append(
            MetricsTarget(
                service="api-service",
                url=_metrics_url(chat_url),
                bearer=(api_bearer or admin_token) or None,
            )
        )
    for item in extra:
        service, sep, url = item.partition("=")
        service = service.strip()
        if not sep or not service or not url.strip():
            raise ValueError(
                f"metrics target {item!r} is not SERVICE=URL (for example "
                "data-service=http://127.0.0.1:8084/metrics)"
            )
        targets.append(
            MetricsTarget(
                service=service,
                url=_metrics_url(url.strip()),
                bearer=admin_token or None,
            )
        )
    # Later declarations win, so an explicit --metrics-target can correct a
    # derived one (a gateway behind a proxy, say) instead of colliding with it.
    deduped: dict[str, MetricsTarget] = {}
    for target in targets:
        deduped[target.service] = target
    return list(deduped.values())


def _metrics_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        raise ValueError(f"cannot derive a /metrics url from {url!r}")
    if parts.path.rstrip("/").endswith("/metrics"):
        return f"{parts.scheme}://{parts.netloc}{parts.path}"
    return f"{parts.scheme}://{parts.netloc}/metrics"


__all__ = [
    "DEFAULT_KEEP_PREFIXES",
    "SCRAPE_TIMEOUT_S",
    "MetricSample",
    "MetricsCollector",
    "MetricsTarget",
    "ScrapeSlice",
    "parse_exposition",
    "scrape_once",
    "targets_from",
]
