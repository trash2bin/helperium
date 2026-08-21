# OpenSpec перед публичной demo: решение об adoption

**Статус:** decision artifact — **не внедрять OpenSpec до публичной demo**.

**Дата:** 2026-08-21.
**Решение:** defer adoption; выполнить bounded one-change pilot только после demo и только при наступлении подходящего trigger.

## Executive decision

OpenSpec — это лёгкий spec-driven workflow: он добавляет в repository canonical `openspec/specs/` и change folders с proposal, delta specs, design и tasks; после archive delta specs сливаются в canonical specs. [1] [2] Это полезно для крупных cross-service changes, где намерение, public contracts и testable scenarios должны быть согласованы до кода.

Для **ближайшей публичной demo Helperium** OpenSpec не является bottleneck и не повышает её runtime safety, CORS correctness, tenant isolation, deploy readiness или качество ответа модели. Эти риски закрываются deployment acceptance, explicit configuration, наблюдаемостью и E2E, перечисленными в [карте отложенных решений](deferred-decisions.md). Внедрение нового process/tooling прямо перед demo создаст дополнительный source-of-truth и process overhead в момент, когда нужно довести delivery path и получить внешнюю обратную связь.

> **Рекомендация:** не устанавливать, не инициализировать и не добавлять `openspec/` в `main` до demo. После demo использовать OpenSpec ровно для одного крупного, рискованного change — предпочтительно reserve/commit spending либо multi-instance shared abuse state — и принять решение по результату этого пилота.

Это не отрицательная оценка OpenSpec. Это decision о **timing**: инструмент окупается при сложном изменении с несколькими durable contracts, а не как prerequisite для уже подготовленной demo.

## Что именно OpenSpec добавит

| Возможность | Реальная польза | Стоимость/обязательство |
|---|---|---|
| `openspec/specs/` | Canonical behavioral requirements по capabilities, с testable scenarios. | Нужно поддерживать актуальность наряду с существующими живыми README, `AGENTS.md`, OpenAPI и runbooks. |
| `openspec/changes/<name>/` | Proposal, design, tasks и delta specs живут вместе с одним изменением. | Каждый значимый change получает additional review surface и archive discipline. |
| `/opsx:explore` | Нулевая по коду exploration для неясной product/architecture проблемы. | Не заменяет существующее code review, research, E2E или решение владельца продукта. |
| `/opsx:propose` → `/opsx:apply` → `/opsx:archive` | Явная связь intent → implementation → updated canonical spec. | Требует, чтобы команда действительно review-ила artifacts, а не генерировала их задним числом. |
| `/opsx:verify` | Проверка completeness/correctness/coherence implementation against artifacts. | Это workflow assistance, не независимый security audit и не доказательство runtime behavior. |

Official OpenSpec documentation описывает `specs/` как source of truth current behavior, а `changes/` — как proposed modifications; archived change merges its delta into the canonical specification. [2] Default workflow остаётся fluid: exploration и verification optional, а artifacts можно обновлять при обучении в ходе реализации. [3]

## Helperium сегодня: где уже есть process value

Repository уже не является «vibe coding without constraints». В нём существуют следующие strong signals:

| Нынешний механизм | Что уже фиксирует |
|---|---|
| `AGENTS.md` | Operating contract, scope, verification baseline, service map, no-write and demo-isolation constraints. |
| Service README и `doc/agents/` | Live runtime behavior, security boundaries, testing/CI, operations и product decisions. |
| OpenAPI + typed DTO + generated dashboard contract | Public API/admin contract and compile-time drift detection. |
| Changelog + small focused commits | Reviewable history of semantic changes. |
| Full CI, clean Docker E2E, live bounded MCP checks | Executable evidence that behavior crosses service boundaries. |
| Existing decision artifacts | Explicit separation of current behavior from deferred product decisions, e.g. backlog and anti-abuse. |

OpenSpec therefore would not supply missing documentation in the abstract. Its potential value is **standardising the lifecycle of future high-risk change proposals** and forcing a reviewable behavior-first contract before code reaches several services.

## Why not immediately before the public demo

### 1. It does not reduce the immediate release risk

The current public-demo release gate is operational: browser behavior on the deployed origin, explicit CORS/embed policy, production secrets, acknowledged control-plane apply, read-only tenant credentials and a bounded incident path. Those items are verified by deployment configuration and actual browser/E2E evidence, not by adding spec folders.

A new spec framework could document those checks, but it cannot perform them. Treating installation as a launch requirement would introduce ceremony without reducing the risk that matters this week.

### 2. It risks creating a second source of truth at the wrong time

OpenSpec explicitly expects canonical specs to describe current system behavior. [2] Helperium already intentionally distinguishes living guides from dated audit evidence. A rushed initial import would force one of two bad outcomes:

| Bad adoption mode | Failure mode |
|---|---|
| Copy existing docs into `openspec/specs/` | Duplicated contracts drift; reviewers must determine whether README, OpenAPI, `AGENTS.md` or OpenSpec wins. |
| Create sparse generic specs | New artifact gives a false impression of coverage while omitting tenant isolation, MCP scope, CORS and no-write guarantees. |

The only sound initial taxonomy requires thought: which behaviors belong in OpenSpec capabilities versus which remain operational guides, generated interface contracts, decision records or archives. That taxonomy is valuable, but it is not demo-critical.

### 3. Adoption needs a real change to measure against

OpenSpec's own guidance targets small-to-medium feature work, unclear design exploration, parallel changes and a review/archive lifecycle. [3] Its value cannot be demonstrated by merely running `openspec init`; that only creates infrastructure. A pilot needs a real change with nontrivial scope, clear acceptance scenarios and a decision that would otherwise be easy to blur in chat.

The upcoming demo should instead serve its purpose: show the product, collect feedback and reveal which next change is actually valuable.

### 4. Default telemetry needs an explicit privacy choice

OpenSpec documents anonymous command-name/version telemetry as enabled by default unless disabled through config or `OPENSPEC_TELEMETRY=0`/`DO_NOT_TRACK=1`. [1] It states that it does not collect arguments, paths, content or PII, but Helperium's project hygiene is intentionally conservative about operational and user data. Any future pilot should explicitly opt out before installation unless the product owner deliberately accepts that telemetry policy.

## Decision matrix

| Criterion | Add OpenSpec now | Defer until after demo | Assessment |
|---|---:|---:|---|
| Improves public demo runtime reliability | Low | Low | Deployment/E2E work, not spec tooling, controls the immediate risk. |
| Improves agent/code change clarity | Medium | High for a suitable major change | Benefit materialises only when artifacts are reviewed and maintained. |
| Adds launch-week work | Medium | None now | New CLI, project structure, ownership rules and migration questions compete with release work. |
| Avoids documentation drift | Low initially | Medium with an explicit taxonomy | A rushed canonical spec layer can increase drift. |
| Helps cross-service security/billing change | Medium | High | Exactly the use case for a one-change pilot. |
| Reversible if it disappoints | Medium | Medium | It is code-free but generated committed artifacts need deliberate cleanup/archival decisions. |
| Privacy/telemetry attention | Required | Required | Explicit opt-out should be part of any pilot. |

**Conclusion:** defer now. The benefit-to-cost ratio for a release-week demo is unfavorable; the benefit-to-cost ratio for the next cross-service, contract-heavy change is favorable enough to test in a bounded pilot.

## What must happen before a pilot

Do not start with `openspec init`. First approve this operating contract:

| Question | Proposed pilot answer |
|---|---|
| Pilot scope | One single logical change only; no retrospective migration of all Helperium docs. |
| Candidate change | `reserve-commit-spending` **or** `multi-instance-abuse-state`, whichever becomes product-relevant first. |
| Canonical behavior ownership | OpenSpec: externally observable capability requirements/scenarios for the pilot. Existing docs: operating instructions, service topology, runbooks and historical evidence. OpenAPI remains canonical for generated API schema. |
| Small fixes | Continue direct tested commits; OpenSpec is not required for typo fixes or isolated regressions. |
| Required artifacts | Lite proposal + delta specs + design + tasks. Avoid custom schema in the first pilot. |
| Review gate | Human approves proposal/spec/design before implementation begins. |
| Test gate | Existing targeted suites, full CI where boundary changes, and Docker E2E remain mandatory; OpenSpec verification supplements rather than replaces them. |
| Archive rule | Archive only after implementation and tests; sync deltas only after checking they match actual code. |
| Telemetry | Set `OPENSPEC_TELEMETRY=0` or `DO_NOT_TRACK=1` before use. |
| Removal rule | If the pilot adds paperwork without preventing ambiguity/rework, do not expand it; decide whether to retain the historical pilot artifacts or remove unadopted scaffolding in an explicit cleanup commit. |

## Pilot execution after demo

When a qualifying trigger arrives, run the smallest viable experiment.

### Step 1 — explore without artifacts

Use OpenSpec's exploration action or an equivalent structured conversation to compare options. `/opsx:explore` is intentionally non-writing and optional. [3] The output must identify business intent, non-goals, interfaces affected, security constraints and test evidence required.

### Step 2 — create one change proposal

Use a precise, stable name such as `reserve-commit-spending` rather than `billing-refactor`. Require:

- `proposal` artifact: product intent, scope, non-goals, alternatives and approval owner;
- delta `specs/`: observable requirements and Given/When/Then scenarios;
- a design artifact: principal model, state machine, storage, idempotency/failure semantics and migration;
- `task list` artifact: independently testable work items and verification steps.

### Step 3 — human review before code

The review must answer: **what does the product promise, what does it explicitly not promise, and how will we know implementation matches that promise?** If the answer remains unclear, revise artifacts; do not use tasks as a substitute for product decision.

### Step 4 — implement and verify with existing gates

Run the current Helperium gates. For cross-service security changes this includes targeted tests, full CI, clean Docker E2E and bounded live tenant MCP verification. Do not claim that `/opsx:verify` replaces these executable controls.

### Step 5 — archive only if it paid off

At completion, evaluate the pilot against measurable qualitative questions:

1. Did the artifact resolve a real ambiguity before code was written?
2. Did it make review/test design faster or catch a drift that code review would have missed?
3. Could a new engineer/agent understand the public behavior from the artifacts without reading chat history?
4. Did artifacts stay aligned with final implementation without disproportionate manual work?
5. Did they coexist cleanly with `AGENTS.md`, OpenAPI, guides and decision records?

Expand adoption only if the answers are predominantly yes.

## Explicit non-goals

- Do not retrofit every historical commit, audit or README into OpenSpec.
- Do not use OpenSpec to replace `AGENTS.md`, security guidance, OpenAPI, tests or CI.
- Do not make slash commands mandatory for one-off fixes.
- Do not enable OpenSpec Stores; they are beta according to the official project, and Helperium has no current multi-repository planning need. [1]
- Do not add custom schemas during the first pilot.
- Do not turn OpenSpec artifacts into an unreviewed AI diary or a duplicate generic task tracker.

## Final recommendation

**Ship the public demo without OpenSpec.** Preserve the current lean process: focused commits, living guides, explicit decision artifacts, CI/Docker E2E and deployed-domain acceptance. Then pilot OpenSpec once on the first post-demo change that genuinely crosses product, security and several service contracts.

This sequencing preserves demo velocity while giving the tool a fair test where it can actually earn its maintenance cost.

## References

[1]: https://github.com/Fission-AI/openspec "Fission-AI/OpenSpec — official repository and installation/telemetry notes"
[2]: https://github.com/Fission-AI/openspec/blob/main/docs/concepts.md "OpenSpec concepts — specs, changes, artifacts and archive model"
[3]: https://github.com/Fission-AI/openspec/blob/main/docs/workflows.md "OpenSpec workflows — explore, propose, apply, verify and archive"
