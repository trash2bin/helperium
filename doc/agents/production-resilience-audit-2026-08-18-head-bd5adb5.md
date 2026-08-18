# Production Resilience & Observability Audit — 2026-08-18

**Scope:** Helperium core platform at `bd5adb5` plus the uncommitted resilience fixes described below. The `demo/autoparts-store` Django/PG storefront was treated as a **foreign consumer** and was not modified. This audit evaluates whether an invite-only/public pre-prod assistant can be detected and restored quickly after a service failure or hostile traffic; it is not a volumetric DDoS test.

## Executive conclusion

> **A normal process crash of `api-service` self-recovers quickly, but the present configuration cannot guarantee that an operator will learn about a sustained outage in under five minutes.** Docker restart policy repaired the tested API process in about five seconds. Prometheus/Grafana/Loki/Tempo provide useful diagnostic material when someone is already looking, but this repository contains no executable alert rules, Alertmanager configuration, notification destination, or external synthetic uptime probe.

The system is therefore suitable for an **attended pre-prod demo** whose operator can inspect it manually. It is **not yet unattended-public-demo ready** for the stated “fell 15 minutes after launch” scenario. Two request-path defects found during the audit were fixed in source and covered with regression tests: the web SSE proxy changed upstream 429 responses into local 500 errors, and proxy-mediated visitors shared one API rate-limit identity.

| Scenario | What was verified | Practical result | Readiness verdict |
|---|---|---|---|
| `api-service` process exits | `kill -TERM 1` inside the API container | Docker `unless-stopped` restarted it; `/health` again returned 200 after **~5 s**; `RestartCount` increased from 0 to 1 | Good automatic local recovery |
| Admin token misconfiguration | Current local `.env` has equal non-empty admin/viewer tokens | Dashboard exited with code 2 and entered a restart loop; other core services remained healthy | Detectable only by local logs/`compose ps`; no alert |
| API limiter reached through web proxy | 35 bounded non-LLM requests to a nonexistent agent | API produced correct 429s after its configured threshold, while old web proxy returned **500** to every caller | Source fixed and unit-tested; rebuild blocked locally by disk |
| Monitoring profile startup | `compose --profile monitoring up` | Required images could not be pulled because Docker registry TLS handshake timed out | Runtime validation of Prometheus/Grafana/Loki/Tempo not completed on this host |
| Volumetric / distributed DDoS | Not attempted | No CDN/WAF/edge rate limit is configured in repository | Not protected at edge; do not claim DDoS readiness |

## System and deployment boundaries

Helperium is the core multi-tenant platform. The autoparts storefront is a separate Django project with its own PostgreSQL and public Caddy; it uses the Helperium widget only when explicitly configured. The public storefront Caddy proxies `/api/*` and `/embed/*` to `HELPERIUM_API_UPSTREAM`, while the core product Caddy proxies to `web:8080`. Both Caddy services bind host ports 80/443, so they **cannot be started on the same host concurrently** without a single shared ingress or separate hosts.

This is not a reason to bake `autoparts` into core seed data. It is a deployment-topology constraint: the storefront may stay external and opt in to the platform through its configured API upstream and agent identifier.

## Evidence: recovery and health behavior

The test started only the local core Compose stack; no production Caddy profile was enabled and the running foreign storefront was not altered. All six HTTP health endpoints subsequently returned 200. A process-level API termination was then injected from inside its own container rather than manually stopping the Docker container. Manual `docker kill` is deliberately treated by Docker as an operator stop and is not a valid restart-policy test.

| Component | Container behavior | Health probe strength | Consequence for incident response |
|---|---|---|---|
| API | `unless-stopped`; tested self-restart | `/health` intentionally returns 200 even when LLM status is `degraded` | Process loss is repaired; LLM/provider outage is not an availability failure to Docker |
| Data service | `unless-stopped` with 10 s checks | tenant health reaches DB ping with a 2 s timeout | Better dependency signal than API |
| RAG | `unless-stopped`, 120 s start period | checks SQLite, ChromaDB and embedding model | Useful but slow initial readiness window |
| MCP gateway | `unless-stopped` | basic `/health` | Process availability only |
| Web proxy | `unless-stopped` | root-page probe | Does not prove an SSE chat request can reach API |
| Admin dashboard | `unless-stopped` | handler always emits `{"status":"ok"}` while process is up | Does not verify data/API/RAG dependencies |

The observed API shutdown was graceful, including closure of MCP sessions and provider-pool health checks. That gives a useful local MTTR for a simple process termination. It does **not** establish recovery from corrupt persistent state, a bad image, a provider outage, disk exhaustion, database unavailability, or a saturation attack.

## Observability assessment

The instrumentation foundation is valuable. Prometheus scrapes the five application services plus itself at 15-second cadence. The provisioned Grafana dashboard includes service health, request/error/latency panels, MCP rate-limit hits, active sessions, LLM usage/cost, abuse blocks and RAG signals. Structured container logs are designed for Loki and traces for Tempo.

However, an observability stack is not an incident-detection system until it has an evaluated condition and a notification path. The repository has no `rule_files`, alerting/Alertmanager configuration, alert rules, or Alertmanager file. The Grafana **Service Health** panel therefore requires a human to have the dashboard open. At a 15-second scrape cadence, Prometheus could show an `up=0` signal shortly after failure, but no repository component pages, messages, emails, or otherwise wakes an operator.

The local monitoring profile could not be started because pulling monitoring images hit a Docker registry TLS handshake timeout. This is an environment limitation, not evidence that dashboard configuration is invalid. It means the end-to-end evidence chain—crash → target down → dashboard/log/trace → operator notification—remains unverified here.

## Abuse and DDoS assessment

The core contains meaningful **application-level** controls, but they are not edge DDoS controls.

| Layer | Present control | Audit finding | Limit |
|---|---|---|---|
| API chat | SlowAPI `CHAT_RATE_LIMIT` (default 30/min) | Original key was proxy peer IP; fixed to use first `X-Forwarded-For` address | In-memory, per instance; only helps after a connection reached the application |
| API chat | per-agent abuse token bucket, session budgets, message length, repeat detection | Original abuse IP also used proxy peer; fixed to share the same client-IP resolver | New sessions can evade session-only limits; persistence/distribution is absent |
| Web proxy | forwards tenant/correlation/auth headers | Now forwards `X-Forwarded-For` too | Must remain behind trusted ingress; direct public API exposure would permit spoofed forwarded headers |
| MCP gateway | 10 RPS / burst 20 token bucket for `/mcp` | Scope is the MCP transport only, not all gateway endpoints | Uses direct remote address; it is an internal service in this topology |
| Data service | global concurrent-request throttle | returns 503 with `Retry-After` when saturated | Back-end containment, not source-side traffic shedding |
| Edge | Caddy TLS, reverse proxy, selected security headers | No Caddy request-rate, body-size, connection or per-IP controls; no CDN/WAF config | A connection/volumetric attack can exhaust host/network capacity before app controls apply |

The bounded probe deliberately used a nonexistent agent, so no LLM request was issued. It established that API itself emitted 429, but the old streamed proxy branch accessed an unread `httpx` streaming response body, raised `ResponseNotRead`, and sent HTTP 500 to the visitor. The fixed code reads the body before forwarding any non-200 response. This preserves the original status and `Retry-After`, so a widget can handle overload accurately instead of reporting a server failure.

## Fixed during this audit

1. **SSE error propagation.** `demo/web/server.py` now uses `await response.aread()` before forwarding non-200 responses from a `stream=True` upstream. A regression test verifies that upstream 429 and `Retry-After` are preserved rather than replaced by 500.
2. **Per-visitor client identity.** `api-service` rate limiting and anti-abuse now use one `get_client_ip()` resolver based on `X-Forwarded-For` with peer-IP fallback. `demo/web` preserves that header towards API. Unit tests cover forwarded and fallback paths.

The source changes pass targeted tests, Python static analysis and formatting. A Docker rebuild of the changed images was attempted but could not finish because Docker ran out of disk space while copying the API build context. The existing runtime was left healthy; no claim is made that the new source was container-verified on this Mac.

## What happens if the demo fails 15 minutes after launch?

If the failure is an ordinary process exit comparable to the injected API termination, Docker restarts it automatically and service health recovers in seconds. If it is a configuration error such as equal dashboard tokens, the component crash-loops indefinitely; logs contain the reason, but no configured alert reports it. If the provider, upstream database, or LLM is degraded while the API process remains alive, several health routes still return HTTP 200, so the existing container checks will not express the visitor-impacting failure.

For an operator already at the terminal, `compose ps`, the structured logs, `docker inspect` restart count and the dashboard design give enough material to narrow a simple service failure quickly. For an unattended operator, the current repository cannot substantiate **MTTD <5 minutes** or a reliable **MTTR <15 minutes**, because detection is manual and runbook escalation/rollback criteria are incomplete.

## Pre-prod gate

| Priority | Required action | Acceptance criterion |
|---|---|---|
| **P0** | Add external synthetic uptime check and an alert destination | A failure of public storefront home, widget asset and API health creates a notification to a person within five minutes; test the notification once |
| **P0** | Add alert rules for `up == 0`, sustained 5xx, restart-loop and error-rate thresholds | Rules are versioned, visible in Prometheus/Grafana and routed to the same destination; include a deliberate test-alert procedure |
| **P0** | Use an actual edge protection service or managed ingress policy | Per-IP request/concurrency limits, body limits and WAF/CDN rules are applied before the single Docker host; API origin is reachable only from trusted ingress |
| **P0** | Deployment preflight for tokens and origins | Startup/deploy rejects equal `ADMIN_TOKEN`/`VIEWER_TOKEN`; public environment has unique secrets, `MCP_REQUIRE_AUTH=true`, explicit CORS/origin values and an encryption key for stored LLM configuration |
| **P0** | Decide the single ingress topology | Do not run root core Caddy and storefront Caddy on one host’s 80/443; document either storefront-owned ingress to Helperium or separate hosts/domains |
| **P1** | Add a synthetic readiness endpoint or probe | It verifies the real visitor path without billable LLM work: ingress → web/API → configured agent/tenant contract, and distinguishes `ready`, `degraded`, `unavailable` |
| **P1** | Make failure runbook operational | One-page first-five-minutes flow: inspect alert, identify component, check restart count/log/trace, safe restart, rollback trigger, owner and communication template |
| **P1** | Rebuild and run the changed Docker path on a clean disk | `web` returns upstream 404/429 unchanged after rebuild; run full Docker E2E and preserve the artifact |
| **P2** | Controlled load test in an isolated environment | Establish a declared load envelope and graceful 429/503 behavior; do not test volumetric DDoS against production |

## Minimal launch drill

Before enabling the public widget, run a 20–30 minute scheduled drill in an isolated pre-prod environment. Confirm that the external monitor alerts on an intentionally stopped API, Docker restart returns traffic, the alert resolves, and logs/traces can be reached using the alert timestamp/correlation identifier. Next, issue a bounded over-limit request sequence and ensure the browser sees 429 plus retry guidance—not a 500—and other test client identities remain usable. Preserve the screenshots/query links and timings as the deployment evidence.

## Verification record

| Check | Result |
|---|---|
| Targeted proxy, rate-limit and anti-abuse tests | **12 passed** |
| `make ci-lint-py` (Ruff check/format + Pyright) | **passed** |
| `git diff --check` | **passed** before report creation |
| `api-service` process crash recovery | **passed**, HTTP 200 after ~5 s |
| Runtime verification of rebuilt Docker images | **blocked by Docker disk exhaustion** |
| Monitoring profile runtime validation | **blocked by Docker image-pull TLS timeout** |
| Volumetric/distributed DDoS testing | **intentionally not performed** |
