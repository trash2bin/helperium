"""Deterministic evaluator — check tool calls, retrieval, answer, hallucination.

Core principle (ТЗ): no LLM judge. Every check is deterministic:

- **tool_ok** — the agent called at least one expected tool (must_call_any),
  and did not call a forbidden one (must_not_call).
- **retrieval_ok** — the expected ground-truth atom appears in tool_results
  (or, for ``not_found`` cases, the tool results are empty).
- **answer_ok** — the final answer contains the expected value (with status
  synonyms for order statuses).
- **hallucination** — any number in the final answer that is not supported
  by tool_results (for ``not_found`` cases: any mention of forbidden data).
- **grounded** — the inverse of hallucination.
- **refusal_ok** — for ``expect_refusal`` cases, the answer has a refusal marker.

The evaluator now produces an explicit verdict (CORRECT/PARTIAL/WRONG/ERROR)
and a stable error taxonomy (ErrorClass) — the benchmark's product contract.

  - verdict: CORRECT | PARTIAL | WRONG | ERROR
  - error_classes: list of ErrorClass values
  - error_source: "agent" | "tool" | "infra" | "bench"
  - budget checks (TOOL_OVERUSE), SKU hallucination, LOST_TOTAL,
    FALSE_UNCERTAINTY, tool-loop, dedupe in min_count.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .models import EvalResult, ErrorClass, RunResult, TestCase, Verdict

# Default status synonyms (order statuses) — can be overridden per-case.
DEFAULT_STATUS_SYNONYMS: dict[str, list[str]] = {
    "shipped": ["отправлен", "в пути", "shipped", "передан в доставку"],
    "delivered": ["доставлен", "получен", "delivered"],
    "processing": ["в обработке", "обрабатывается", "processing"],
    "new": ["новый", "new", "создан"],
    "confirmed": ["подтверждён", "confirmed"],
    "cancelled": ["отменён", "cancelled"],
}

# Markers that indicate a refusal / not-found answer.
REFUSAL_MARKERS = [
    "не найден",
    "не найдено",
    "нет в наличии",
    "отсутствует",
    "не удалось найти",
    "не существует",
    "нет такого",
    "не могу найти",
    "не нашёл",
    "ничего не найдено",
]

# markers of false uncertainty — the agent says "скорее всего" although
# the fact was precisely grounded in tool_results.
UNCERTAINTY_MARKERS = [
    "скорее всего",
    "вероятно",
    "наверное",
    "возможно",
    "вроде",
    "кажется",
    "предположительно",
    "наверняка",
    "похоже",
    "пожалуй",
]

# vague quantity markers — the agent knows total:N but says "много".
VAGUE_COUNT_MARKERS = [
    "много",
    "несколько",
    "разные",
    "и другие",
    "ещё есть",
    "есть ещё",
    "некоторые",
    "ряд позиций",
    "целый ряд",
    "большое количество",
    "немало",
]

# SKU/article pattern (EXT-01392, BRK-01004, АП-100005).
_SKU_RE = re.compile(r"\b(?:[A-Za-zА-Яа-яЁё]{2,4}-\d{3,6})\b")

# keys in a tool_result payload that indicate an error (not data).
_ERROR_KEYS = ("error", "error_code", "error_message", "exception", "stack")
# keys in a tool_result payload that indicate data (rows/preview/total).
_DATA_KEYS = ("preview", "rows", "items", "data", "results", "total", "returned", "count")

# Numbers that are structural (durations, ids in "id: N" labels, order numbers)
# and should not be treated as answer facts.  We only compare against *values*
# present in tool_results, so the risk is low; but we skip obviously structural
# substrings like "№ 123" or article-like codes.
_NUMBER_RE = re.compile(r"\d{1,3}(?:[\s\u00a0]\d{3})*|\d+")

# Valid entity names that the agent may pass as ``entity`` to db_* tools.
# Anything else (e.g. "Order" instead of "catalog_order") is a schema-reading
# failure the benchmark should surface.
VALID_ENTITIES = {
    "catalog_product",
    "catalog_order",
    "catalog_brand",
    "catalog_category",
    "catalog_cart",
    "catalog_cartitem",
    "catalog_sitesettings",
}


class DeterministicEvaluator:
    """Deterministic checks for one case + run result."""

    def __init__(self, status_synonyms: dict[str, list[str]] | None = None) -> None:
        self.status_synonyms = status_synonyms or DEFAULT_STATUS_SYNONYMS

    # ── public API ─────────────────────────────────────────────────────────

    def evaluate(self, case: TestCase, run: RunResult) -> EvalResult:
        """Evaluate one case against one run result."""
        reasons: list[str] = []
        error_classes: list[str] = []

        # ── infra/tool error detection first ────────────────────────────────
        tool_errors = self._detect_tool_errors(run)
        has_tool_error = len(tool_errors) > 0
        if has_tool_error:
            error_classes.append(ErrorClass.INFRA_ERROR)

        tool_ok = self._check_tool_calls(case, run.tool_calls, reasons)
        if not tool_ok and case.expected_tool:
            must_not = case.expected_tool.get("must_not_call", [])
            if must_not:
                called = {tc.get("name", "") for tc in run.tool_calls}
                if called & set(must_not):
                    error_classes.append(ErrorClass.FORBIDDEN_TOOL)

        entity_name_ok = self._check_entity_names(run.tool_calls, reasons)
        if not entity_name_ok:
            error_classes.append(ErrorClass.SCHEMA_ENTITY_ERROR)

        retrieval_ok = self._check_retrieval(case, run.tool_results, reasons)
        if not retrieval_ok:
            error_classes.append(ErrorClass.RETRIEVAL_MISS)

        answer_ok = self._check_answer(case, run.final_text, reasons)
        if not answer_ok:
            error_classes.append(ErrorClass.ANSWER_MISS)

        # number hallucination + SKU hallucination (independent checks).
        # SKU may be hallucinated even when numbers are all grounded.
        hallucination = self._check_hallucination(case, run, reasons)
        sku_hallucination = self._check_sku_hallucination(case, run)
        if hallucination or sku_hallucination:
            if sku_hallucination and not hallucination:
                error_classes.append(ErrorClass.HALLUCINATED_SKU)
            elif hallucination and not sku_hallucination:
                error_classes.append(ErrorClass.HALLUCINATED_NUMBER)
            else:
                # Both — record both
                error_classes.append(ErrorClass.HALLUCINATED_SKU)
                error_classes.append(ErrorClass.HALLUCINATED_NUMBER)
            hallucination = True  # any hallucination flips the flag

        # WRONG_AVAILABILITY / WRONG_STATUS — inferred from expected keys
        self._check_wrong_fact_type(case, run, answer_ok, error_classes, reasons)

        refusal_ok = self._check_refusal(case, run.final_text, reasons)
        if not refusal_ok:
            error_classes.append(ErrorClass.REFUSAL_MISSING)

        # LOST_TOTAL — total:N known in tools, answer vague
        lost_total = self._check_lost_total(case, run, reasons)
        if lost_total:
            error_classes.append(ErrorClass.LOST_TOTAL)
            # LOST_TOTAL is a major defect, not a critical answer miss —
            # drop the ANSWER_MISS class (the agent *did* retrieve, just lost the number).
            if ErrorClass.ANSWER_MISS in error_classes:
                error_classes.remove(ErrorClass.ANSWER_MISS)

        # FALSE_UNCERTAINTY
        false_unc = self._check_false_uncertainty(case, run, reasons)
        if false_unc:
            error_classes.append(ErrorClass.FALSE_UNCERTAINTY)

        # TOOL_OVERUSE — budget from case
        overuse = self._check_budget(case, run, reasons)
        if overuse:
            error_classes.append(ErrorClass.TOOL_OVERUSE)

        # TOOL_LOOP
        if run.loop_warnings:
            error_classes.append(ErrorClass.TOOL_LOOP)

        # verdict + error_source
        error_source = self._resolve_error_source(has_tool_error, run)
        verdict = self._compute_verdict(
            case, run, error_classes, has_tool_error,
            tool_ok, retrieval_ok, answer_ok, hallucination, refusal_ok,
        )

        return EvalResult(
            case_id=case.id,
            tool_ok=tool_ok,
            retrieval_ok=retrieval_ok,
            answer_ok=answer_ok,
            hallucination=hallucination,
            grounded=not hallucination,
            refusal_ok=refusal_ok,
            entity_name_ok=entity_name_ok,
            reasons=reasons,
            verdict=verdict,
            error_classes=error_classes,
            error_source=error_source,
            total_tool_calls=len(run.tool_calls),
            repeated_tool_calls=self._count_repeated_tool_calls(run),
            unique_tool_calls=self._count_unique_tool_calls(run),
            db_get_count=self._count_db_get(run),
            llm_calls=run.backlog.llm_calls if run.backlog else 0,
            total_tokens=run.backlog.total_tokens if run.backlog else 0,
            cost_usd=run.backlog.total_cost if run.backlog else 0.0,
            duration_ms=run.backlog.duration_ms if run.backlog else 0.0,
            loop_warnings=run.loop_warnings,
        )

    # ── verdict / error_source / helpers ────────────────────────────────

    @staticmethod
    def _detect_tool_errors(run: RunResult) -> list[dict[str, Any]]:
        """Return tool-result payloads that are actually error responses.

        Tool errors are distinct from agent errors: the agent may have made a
        correct call, but the service returned ``{"error": "timeout"}``. We
        treat those as INFRA_ERROR, not as a wrong answer.
        """
        errors: list[dict[str, Any]] = []
        for tr in run.tool_results:
            raw = tr.get("result", "")
            if not raw:
                continue
            if isinstance(raw, str):
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    obj = json.loads(stripped)
                except (json.JSONDecodeError, ValueError):
                    # Non-JSON non-empty — could be error text
                    if any(marker in stripped.lower() for marker in ("error", "timeout", "failed", "exception")):
                        errors.append(tr)
                    continue
            else:
                obj = raw
            if isinstance(obj, dict) and any(k in obj for k in _ERROR_KEYS):
                errors.append(tr)
        return errors

    @staticmethod
    def _resolve_error_source(has_tool_error: bool, run: RunResult) -> str:
        """Classify where the failure came from."""
        if has_tool_error:
            return "tool"
        if run.errors:
            # HTTP/network/request-level errors from runner
            return "infra"
        return "agent"

    @staticmethod
    def _count_repeated_tool_calls(run: RunResult) -> int:
        """Count calls that repeat the previous call (same name+args)."""
        seq = run.tool_call_sequence or run.tool_calls
        if len(seq) < 2:
            return 0
        count = 0
        prev = None
        for tc in seq:
            key = (tc.get("name", ""), str(tc.get("arguments", {})))
            if prev is not None and key == prev:
                count += 1
            prev = key
        return count

    @staticmethod
    def _count_unique_tool_calls(run: RunResult) -> int:
        """Count distinct (name, args) pairs."""
        seq = run.tool_call_sequence or run.tool_calls
        return len({(tc.get("name", ""), str(tc.get("arguments", {}))) for tc in seq})

    @staticmethod
    def _count_db_get(run: RunResult) -> int:
        """Count db_get calls (fanout indicator)."""
        seq = run.tool_call_sequence or run.tool_calls
        return sum(1 for tc in seq if tc.get("name") == "db_get")

    # ── SKU hallucination ──────────────────────────────────────────────

    def _check_sku_hallucination(self, case: TestCase, run: RunResult) -> bool:
        """True if the final answer contains a SKU not present in tool_results.

        SKU pattern: EXT-01392, BRK-01004, АП-100005. Numbers alone are not
        SKUs (handled by _extract_numbers). This catches hallucinated article
        codes — often more severe than a wrong price.
exclude (a) SKUs that appear in the *question* (the agent may
        legitimately restate them), and (b) absence/not_found cases (the
        agent names the non-existent SKU as part of the refusal).
        """
        gt = case.ground_truth or {}
        if gt.get("type") == "not_found":
            return False
        # SKU-проверка включается только если кейс задаёт ground truth SKU
        # (any_of_skus) или явный флаг. Иначе мы не можем отличить пересказ
        # артикула из вопроса/данных от выдумки → false positives.
        if not gt.get("any_of_skus") and not gt.get("check_skus"):
            return False
        answer_skus = set(_SKU_RE.findall(run.final_text))
        if not answer_skus:
            return False
        question_skus = set(_SKU_RE.findall(run.question))
        answer_skus -= question_skus
        if not answer_skus:
            return False
        # tool_text может содержать JSON с экранированной кириллицей
        # (\u0410\u041f-100005). Декодируем unicode-escape перед SKU-поиском,
        # иначе кириллические артикулы (АП-100005) не матчатся в tool_results.
        def _decode(s: str) -> str:
            try:
                return s.encode("utf-8").decode("unicode_escape")
            except Exception:
                return s
        tool_text = " ".join(str(r.get("result", "")) for r in run.tool_results)
        tool_text = _decode(tool_text)
        tool_skus = set(_SKU_RE.findall(tool_text))
        allowed = set(gt.get("any_of_skus", []) or [])
        # SKU рядом с арифметикой („FLT-01188: 2031 ₽ (677×3)") — это
        # перенос подтверждённых данных, а не выдумка. Такие SKU не считаем.
        context_skus = set()
        for m in _SKU_RE.finditer(run.final_text):
            tail = run.final_text[m.end():m.end() + 60]
            if any(mk in tail for mk in ("×", "*", "+", "-", "=", "₽", "руб", "итого", "позиц")):
                context_skus.add(m.group(0))
        unsupported = answer_skus - tool_skus - allowed - context_skus
        return bool(unsupported)

    # ── LOST_TOTAL ─────────────────────────────────────────────────────

    def _check_lost_total(self, case: TestCase, run: RunResult, reasons: list[str]) -> bool:
        """True if the tools returned total:N, the answer omits it and is vague.

        This is the Camry-class defect: agent retrieved 40 but said "много".
        """
        gt = case.ground_truth or {}
        if gt.get("type") == "not_found":
            return False

        # total:N present in tool results?
        total_value: int | None = None
        for tr in run.tool_results:
            raw = tr.get("result", "")
            if isinstance(raw, str):
                try:
                    obj = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
            else:
                obj = raw
            if isinstance(obj, dict) and isinstance(obj.get("total"), (int, float)):
                total_value = int(obj["total"])
                break

        if total_value is None:
            return False

        # Must the answer mention the total? Only if expected or answer_rules say so.
        expected = gt.get("expected", {})
        rules = case.ground_truth.get("answer_rules", {}) or {}
        require_total = rules.get("expect_total_mentioned", False) or (
            isinstance(expected.get("count"), (int, float)) and int(expected["count"]) == total_value
        )
        if not require_total:
            return False

        text_norm = self._normalize_text(run.final_text)
        if str(total_value) in text_norm:
            return False  # total mentioned — fine

        # Vague instead of exact number?
        if any(m in text_norm for m in VAGUE_COUNT_MARKERS):
            reasons.append(
                f"LOST_TOTAL: tools returned total={total_value}, answer is vague (no '{total_value}')"
            )
            return True
        # Also flag if answer is non-empty but omits the number entirely
        if run.final_text.strip():
            reasons.append(
                f"LOST_TOTAL: tools returned total={total_value}, answer omits it"
            )
            return True
        return False

    # ── FALSE_UNCERTAINTY ──────────────────────────────────────────────

    def _check_false_uncertainty(self, case: TestCase, run: RunResult, reasons: list[str]) -> bool:
        """True if an uncertainty marker sits next to a fact that was grounded.

        Detector: find uncertainty markers ("скорее всего", "вероятно"...) in
        the answer, then check whether the fact near the marker is supported by
        tool_results. Only flags when a *grounded* fact is hedged.
        """
        text_lower = run.final_text.lower()
        if not any(m in text_lower for m in UNCERTAINTY_MARKERS):
            return False

        # Skip if the case is inherently uncertain (e.g. open question)
        if case.ground_truth.get("type") == "not_found":
            return False

        # The fact is grounded if retrieval found expected atom
        if not self._retrieval_has_ground_truth(case, run):
            return False

        # We have a grounded fact + uncertainty marker → false uncertainty
        reasons.append("FALSE_UNCERTAINTY: grounded fact hedged with uncertainty marker")
        return True

    def _retrieval_has_ground_truth(self, case: TestCase, run: RunResult) -> bool:
        """Cheap check: does tool_results contain the expected ground-truth atom?"""
        gt = case.ground_truth or {}
        expected = gt.get("expected", {})
        if not expected:
            return False
        aliases = gt.get("country_aliases", [])
        for tr in run.tool_results:
            text = str(tr.get("result", ""))
            if text and self._value_in_text(expected, text, country_aliases=aliases):
                return True
        return False

    # ── budget / overuse ───────────────────────────────────────────────

    def _check_budget(self, case: TestCase, run: RunResult, reasons: list[str]) -> bool:
        """Check efficiency budget: max_tool_calls, max_db_get, max_llm_calls, max_tokens, max_cost."""
        budget = case.budget or {}
        if not budget:
            return False

        tool_calls = len(run.tool_calls)
        db_get = self._count_db_get(run)
        llm = run.backlog.llm_calls if run.backlog else 0
        tokens = run.backlog.total_tokens if run.backlog else 0
        cost = run.backlog.total_cost if run.backlog else 0.0

        violated = False
        if "max_tool_calls" in budget and tool_calls > budget["max_tool_calls"]:
            reasons.append(f"TOOL_OVERUSE: {tool_calls} tool calls > {budget['max_tool_calls']}")
            violated = True
        if "max_db_get" in budget and db_get > budget["max_db_get"]:
            reasons.append(f"TOOL_OVERUSE: {db_get} db_get > {budget['max_db_get']}")
            violated = True
        if "max_llm_calls" in budget and llm > budget["max_llm_calls"]:
            reasons.append(f"TOOL_OVERUSE: {llm} llm calls > {budget['max_llm_calls']}")
            violated = True
        if "max_tokens" in budget and tokens > budget["max_tokens"]:
            reasons.append(f"TOOL_OVERUSE: {tokens} tokens > {budget['max_tokens']}")
            violated = True
        if "max_cost_usd" in budget and cost > budget["max_cost_usd"]:
            reasons.append(f"TOOL_OVERUSE: cost ${cost:.4f} > ${budget['max_cost_usd']}")
            violated = True
        return violated

    # ── wrong fact type (availability/status) ──────────────────────────

    def _check_wrong_fact_type(
        self,
        case: TestCase,
        run: RunResult,
        answer_ok: bool,
        error_classes: list[str],
        reasons: list[str],
    ) -> None:
        """If expected has availability/status keys and answer is wrong → specific class."""
        expected = (case.ground_truth or {}).get("expected", {})
        if not expected or answer_ok:
            return
        if any(k in expected for k in ("available", "is_available", "in_stock")):
            error_classes.append(ErrorClass.WRONG_AVAILABILITY)
        if "status" in expected:
            error_classes.append(ErrorClass.WRONG_STATUS)

    # ── verdict computation ────────────────────────────────────────────

    def _compute_verdict(
        self,
        case: TestCase,
        run: RunResult,
        error_classes: list[str],
        has_tool_error: bool,
        tool_ok: bool,
        retrieval_ok: bool,
        answer_ok: bool,
        hallucination: bool,
        refusal_ok: bool,
    ) -> Verdict:
        """Map boolean checks + error classes to CORRECT/PARTIAL/WRONG/ERROR.

        - ERROR: infra/tool/bench failure (cannot evaluate).
        - WRONG: critical factual error or hallucination.
        - PARTIAL: no critical, but major/minor defects (lost total,
          false uncertainty, overuse, schema, loop...).
        - CORRECT: everything clean.
        """
        # ERROR: infra/tool/bench failure
        if has_tool_error or run.errors:
            return Verdict.ERROR
        if not run.final_text.strip() and not run.tool_calls:
            return Verdict.ERROR

        # WRONG: critical
        if hallucination:
            return Verdict.WRONG
        # LOST_TOTAL: retrieved but lost the number → major (PARTIAL), not critical
        if ErrorClass.LOST_TOTAL in error_classes:
            return Verdict.PARTIAL
        if not answer_ok or not retrieval_ok:
            return Verdict.WRONG
        if not refusal_ok:
            return Verdict.WRONG

        # PARTIAL: major/minor without critical
        minor = {
            ErrorClass.LOST_TOTAL,
            ErrorClass.FALSE_UNCERTAINTY,
            ErrorClass.TOOL_OVERUSE,
            ErrorClass.TOOL_LOOP,
            ErrorClass.SCHEMA_ENTITY_ERROR,
            ErrorClass.FORBIDDEN_TOOL,
        }
        if any(cls in error_classes for cls in minor):
            return Verdict.PARTIAL

        # entity name error alone → PARTIAL (not critical fact)
        if not tool_ok:
            return Verdict.PARTIAL

        return Verdict.CORRECT

    # ── entity names ───────────────────────────────────────────────────────

    def _check_entity_names(
        self,
        tool_calls: list[dict[str, Any]],
        reasons: list[str],
    ) -> bool:
        """Check the agent uses valid entity names in ``entity`` args.

        Models often send ``entity="Order"`` instead of ``catalog_order``
        (schema-reading failure).  Any invalid entity name → False.
        """
        for tc in tool_calls:
            args = tc.get("arguments", {})
            if not isinstance(args, dict):
                continue
            entity = args.get("entity", "")
            if entity and entity not in VALID_ENTITIES:
                reasons.append(f"invalid entity name: {entity!r} (valid: {sorted(VALID_ENTITIES)[:3]}...)")
                return False
        return True

    # ── tool calls ─────────────────────────────────────────────────────────

    def _check_tool_calls(
        self,
        case: TestCase,
        tool_calls: list[dict[str, Any]],
        reasons: list[str],
    ) -> bool:
        """Agent called at least one must_call_any tool and no must_not_call."""
        called_names = {tc.get("name", "") for tc in tool_calls}
        if not called_names:
            reasons.append("no tool calls")
            return False

        exp = case.expected_tool or {}
        must_any = exp.get("must_call_any", [])
        must_not = exp.get("must_not_call", [])

        if must_any:
            hit = called_names & set(must_any)
            if not hit:
                reasons.append(f"expected tool in {must_any}, called {sorted(called_names)}")
                return False
        if must_not:
            bad = called_names & set(must_not)
            if bad:
                reasons.append(f"forbidden tool called: {sorted(bad)}")
                return False
        return True

    # ── retrieval ──────────────────────────────────────────────────────────

    def _check_retrieval(
        self,
        case: TestCase,
        tool_results: list[dict[str, Any]],
        reasons: list[str],
    ) -> bool:
        """Expected atom present in tool_results (or empty for not_found)."""
        gt = case.ground_truth or {}
        gt_type = gt.get("type", "")

        if gt_type == "not_found":
            ok = self._check_empty_results(tool_results)
            if not ok:
                reasons.append("absence case: tool results were NOT empty")
            return ok

        expected = gt.get("expected", {})

        # list_ids: require at least N unique rows across tool results
        if "min_count" in expected:
            return self._check_min_rows(tool_results, int(expected["min_count"]), reasons)

        # Look for expected values in the concatenated tool result text
        aliases = gt.get("country_aliases", [])
        for result in tool_results:
            text = str(result.get("result", ""))
            if text and self._value_in_text(expected, text, country_aliases=aliases):
                return True

        reasons.append(f"expected {expected} not found in tool results")
        return False

    def _check_min_rows(
        self,
        tool_results: list[dict[str, Any]],
        min_count: int,
        reasons: list[str],
    ) -> bool:
        """True if tool results contain at least ``min_count`` **unique** rows.
dedupe by ``(entity, id)`` / ``article`` — an agent that does
        ``db_search`` (preview 20) then 9x ``db_get`` on the same items should
        not double/triple-count the same products.
        """
        seen: set[tuple[str, Any]] = set()
        total = 0
        for result in tool_results:
            raw = result.get("result", "")
            entity = result.get("name", "")
            rows = self._extract_row_identities(raw, entity)
            if rows is None:
                # Unknown structure — fall back to non-empty text = 1 row
                if str(raw).strip() not in ("", "[]", "{}", "null"):
                    if (entity, "__raw__") not in seen:
                        seen.add((entity, "__raw__"))
                        total += 1
                continue
            for ident in rows:
                if ident not in seen:
                    seen.add(ident)
                    total += 1
        ok = total >= min_count
        if not ok:
            reasons.append(f"expected >= {min_count} rows in tool results, got {total} (unique)")
        return ok

    def _extract_row_identities(self, raw: Any, entity: str = "") -> list[tuple[str, Any]] | None:
        """Extract unique row identities from a tool result.

        Returns a list of ``(entity, id)`` / ``(entity, article)`` tuples, or
        None if the structure is not parseable (caller falls back).
        """
        if isinstance(raw, str):
            stripped = raw.strip()
            if not stripped:
                return []
            try:
                obj = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                return None
        else:
            obj = raw
        if obj is None:
            return []
        if isinstance(obj, list):
            out = []
            for item in obj:
                if isinstance(item, dict):
                    ident = self._row_identity(item, entity)
                    if ident:
                        out.append(ident)
                else:
                    out.append((entity, item))
            return out
        if isinstance(obj, dict):
            if "empty_hint" in obj or any(k in obj for k in _ERROR_KEYS):
                return []
            for key in ("preview", "rows", "items", "data", "results"):
                if key in obj and isinstance(obj[key], list):
                    out = []
                    for item in obj[key]:
                        if isinstance(item, dict):
                            ident = self._row_identity(item, entity)
                            if ident:
                                out.append(ident)
                        else:
                            out.append((entity, item))
                    return out
            # одиночный dict — одна строка (db_get {id, price, ...})
            ident = self._row_identity(obj, entity)
            if ident:
                return [ident]
        return None

    @staticmethod
    def _row_identity(item: dict[str, Any], entity: str) -> tuple[str, Any] | None:
        """Identity of a row: (entity, id) or (entity, article)."""
        if "id" in item and item["id"] is not None:
            return (entity, item["id"])
        if "article" in item and item["article"] is not None:
            return (entity, item["article"])
        return None

    @staticmethod
    def _count_rows(raw: Any) -> int | None:
        """Count rows in a tool result if structured; None if not parseable."""
        if isinstance(raw, str):
            stripped = raw.strip()
            if not stripped:
                return 0
            try:
                obj = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                return None
        else:
            obj = raw
        if obj is None:
            return 0
        if isinstance(obj, list):
            return len(obj)
        if isinstance(obj, dict):
            if "empty_hint" in obj:
                return 0
            # error payload is not data
            if any(k in obj for k in _ERROR_KEYS):
                return 0
            for key in ("preview", "rows", "items", "data", "results"):
                if key in obj and isinstance(obj[key], list):
                    return len(obj[key])
            for key in ("total", "returned", "count"):
                if key in obj and isinstance(obj[key], (int, float)):
                    return int(obj[key])
        return None

    def _check_empty_results(self, tool_results: list[dict[str, Any]]) -> bool:
        """True if all tool results are empty (no rows).

        Handles both raw empties ("[]", "{}", null) and structured previews
        like ``{"preview": [], "returned": 0, "total": 0}`` (data-service
        filter/search format) — a result with no rows counts as empty.
        """
        if not tool_results:
            return True
        for result in tool_results:
            name = result.get("name", "")
            # Discovery-тулы (db_map/db_describe) не считаются данными — они
            # возвращают схему/метаданные, не строки. Absence-кейс: модель
            # зовёт db_map, получает непустую карту → это НЕ retrieval.
            if name in ("db_map", "db_describe"):
                continue
            raw = result.get("result", "")
            if isinstance(raw, str):
                stripped = raw.strip()
                if stripped in ("[]", "{}", "null", "", "None"):
                    continue
                # Try to parse structured JSON preview
                try:
                    obj = json.loads(stripped)
                except (json.JSONDecodeError, ValueError):
                    return False  # non-JSON non-empty text = data
                if self._json_has_rows(obj):
                    return False
            else:
                if self._json_has_rows(raw):
                    return False
        return True

    @staticmethod
    def _json_has_rows(obj: Any) -> bool:
        """True if a parsed JSON object contains actual rows.

        Handles: list (non-empty), dict with ``preview``/``rows``/``items``
        keys (non-empty lists), or ``total``/``returned`` > 0.
        """
        if obj is None or obj == {}:
            return False
        if isinstance(obj, list):
            return len(obj) > 0
        if isinstance(obj, dict):
            # error payload is not data
            if any(k in obj for k in _ERROR_KEYS):
                return False
            # Empty-hint from data-service: no rows, just field suggestions
            if "empty_hint" in obj:
                return False
            # Structured data-service response
            if any(k in obj for k in ("preview", "rows", "items", "data", "results")):
                for key in ("preview", "rows", "items", "data", "results"):
                    if key in obj and isinstance(obj[key], list) and len(obj[key]) > 0:
                        return True
                return False
            if any(k in obj for k in ("total", "returned", "count")):
                for key in ("total", "returned", "count"):
                    if key in obj and isinstance(obj[key], (int, float)) and obj[key] > 0:
                        return True
                return False
            # Plain dict with keys = a single row
            if obj:
                return True
        return False

    # ── answer ─────────────────────────────────────────────────────────────

    def _check_answer(
        self,
        case: TestCase,
        final_text: str,
        reasons: list[str],
    ) -> bool:
        """Final answer contains the expected atom (with synonyms for status)."""
        gt = case.ground_truth or {}
        gt_type = gt.get("type", "")

        if gt_type == "not_found":
            # Absence: answer must NOT contain forbidden data
            forbidden = gt.get("forbidden", {})
            if forbidden and self._value_in_text(forbidden, final_text):
                reasons.append("absence case: answer contains forbidden data")
                return False
            return True

        expected = gt.get("expected", {})
        if not expected:
            return True  # nothing to check

        # list_ids: no exact value to match — answer should just be non-empty
        if "min_count" in expected:
            ok = bool(final_text and final_text.strip())
            if not ok:
                reasons.append("list_ids case: final answer is empty")
            return ok

        # Status with synonyms
        if "status" in expected:
            syns = case.status_synonyms or self.status_synonyms
            status = str(expected["status"])
            ok = self._check_status_with_synonyms(status, syns, final_text)
            if not ok:
                reasons.append(f"status '{status}' (synonyms {syns.get(status)}) not in answer")
            return ok

        # Exact value / count — direct substring match
        ok = self._value_in_text(expected, final_text, country_aliases=gt.get("country_aliases", []))
        if not ok:
            reasons.append(f"expected {expected} not in final answer")
        return ok

    def _check_status_with_synonyms(
        self,
        status: str,
        synonyms: dict[str, list[str]],
        text: str,
    ) -> bool:
        text_lower = text.lower()
        for syn in synonyms.get(status, [status]):
            if syn.lower() in text_lower:
                return True
        return False

    # ── bool semantic matching ───────────────────
    # Бенч: GT {"available": true} ищет literal "true", а модель пишет
    # «в наличии» → false-negative. Bool-ключи матчатся семантически.
    # убраны слабые маркеры (да/есть/нет/1/0) — слишком широкие (false positives).
    _BOOL_TRUE_MARKERS = [
        "в наличии", "есть в наличии", "имеется в наличии", "доступен",
        "доступно", "на складе", "в наличии на складе", "true",
    ]
    _BOOL_FALSE_MARKERS = [
        "нет в наличии", "отсутствует", "недоступен", "недоступно",
        "закончился", "закончилась", "нет на складе", "снят с продажи",
        "не в наличии", "false",
    ]
    _BOOL_KEYS = {"available", "is_available", "in_stock", "is_active", "active"}

    @staticmethod
    def _match_bool(value: bool, text_norm: str) -> bool:
        """True→«в наличии/доступен», False→«закончился/нет». По подстроке.
негативные маркеры проверяются ПЕРВЫМИ для обоих значений —
        «нет в наличии» содержит «в наличии», и для value=True это не должно
        матчиться. Если найден сильный негатив → ответ = False, независимо
        от value.
        """
        # Strong negative markers override positive ones
        for neg in DeterministicEvaluator._BOOL_FALSE_MARKERS:
            if neg in text_norm:
                # Found explicit "нет в наличии" — the fact is False
                return value is False
        for pos in DeterministicEvaluator._BOOL_TRUE_MARKERS:
            if pos in text_norm:
                return value is True
        return False

    # ── hallucination / groundedness ───────────────────────────────────────

    def _check_hallucination(
        self,
        case: TestCase,
        run: RunResult,
        reasons: list[str],
    ) -> bool:
        """Any unsupported fact (number) in the final answer."""
        gt = case.ground_truth or {}
        if gt.get("type") == "not_found":
            # Absence case: hallucination = answered with data although
            # retrieval was empty.  (If we already marked refusal_ok=False
            # — the agent invented availability — that is a hallucination.)
            if not self._check_refusal(case, run.final_text, []):
                reasons.append("hallucination: absence case invented data (no refusal)")
                return True
            forbidden = gt.get("forbidden", {})
            if forbidden and self._value_in_text(forbidden, run.final_text):
                reasons.append("hallucination: absence case answered with forbidden data")
                return True
            return False

        answer_numbers = self._extract_numbers(run.final_text)
        if not answer_numbers:
            return False

        # Numbers that appear in the question are NOT hallucinations (the
        # agent may legitimately restate them: "дороже 3000", "бренд Hella 5")
        question_numbers = set(self._extract_numbers(run.question))

        tool_text = " ".join(str(r.get("result", "")) for r in run.tool_results)
        tool_numbers = set(self._extract_numbers(tool_text))

        unsupported = [
            n for n in answer_numbers
            if n not in tool_numbers and n not in question_numbers
        ]
        # Проценты ("скидка ~20%", "-30% к цене") — производные расчёты модели
        # от подтверждённых цен (old_price→price). Не галлюцинация.
        percent_numbers = self._extract_percents(run.final_text)
        unsupported = [n for n in unsupported if n not in percent_numbers]
        # : производные числа (суммы "677×3=2031", "плюс ещё 5",
        # "итого 7809") — модель считает их из подтверждённых данных.
        derived = self._extract_derived_numbers(run.final_text)
        unsupported = [n for n in unsupported if n not in derived]
        # : числа, выразимые как произведение/сумма подтверждённых
        # (2031 = 677×3, где 677 и 3 в tool_numbers) — производные, не галлюцинация.
        derived_math = self._derive_from_tool_numbers(unsupported, tool_numbers)
        unsupported = [n for n in unsupported if n not in derived_math]
        if unsupported:
            reasons.append(f"unsupported numbers in answer: {unsupported[:5]}")
            return True
        return False

    @staticmethod
    def _derive_from_tool_numbers(unsupported: list[str], tool_numbers: set[str]) -> set[str]:
        """Числа, выразимые как произведение/сумма подтверждённых — ТОЛЬКО при
        явном арифметическом контексте.

        произвольные пары больших чисел НЕ прощаются —
        700 = 20×35 формально derived, но по смыслу выдумка (агент не обязан
        был перемножать любые числа). Прощаются только:
        - произведение, где один множитель малый (1..9), а второй подтверждён
          в tool_results — line-item цена×кол-во (2031 = 677×3, 677 в тулах);
        - сумма ≤ 3 слагаемых, где хотя бы одно малое (1..9) —
          кол-во позиций/строк (итого 7809 = 677×3 + 4585×1).
        Малые 1..9 вводятся независимо от tool_numbers (однозначные отсекаются
        _extract_numbers), но работают только в паре с подтверждённым большим.
        """
        nums = sorted(int(n) for n in tool_numbers if n.isdigit() and len(n) <= 9)
        if not nums:
            return set()
        small = {i for i in range(1, 10)}

        # Произведения: ровно один малый множитель (1..9), второй —
        # подтверждённый большой. Убирает 700 = 20×35 (оба больших),
        # оставляет 2031 = 677×3 (677 подтверждён, 3 — малый).
        products: set[int] = set()
        for big in nums:
            for s in small:
                if big in small:
                    continue  # оба малых — не line-item
                p = big * s
                if p <= 1_000_000:
                    products.add(p)

        # Суммы пар/троек: хотя бы одно слагаемое малое (1..9), остальные
        # подтверждённые — кол-во строк/позиций, не произвольные большие.
        sums: set[int] = set()
        for i in range(len(nums)):
            for j in range(i + 1, len(nums)):
                if nums[i] in small or nums[j] in small:
                    sums.add(nums[i] + nums[j])
        for i in range(len(nums)):
            for j in range(i + 1, len(nums)):
                for k in range(j + 1, len(nums)):
                    if nums[i] in small or nums[j] in small or nums[k] in small:
                        sums.add(nums[i] + nums[j] + nums[k])

        # Суммы пар/троек: хотя бы одно слагаемое малое (1..9) —
        # кол-во строк/позиций, а не произвольные большие числа.
        sums: set[int] = set()
        for i in range(len(nums)):
            for j in range(i + 1, len(nums)):
                if nums[i] in small or nums[j] in small:
                    sums.add(nums[i] + nums[j])
        for i in range(len(nums)):
            for j in range(i + 1, len(nums)):
                for k in range(j + 1, len(nums)):
                    if nums[i] in small or nums[j] in small or nums[k] in small:
                        sums.add(nums[i] + nums[j] + nums[k])

        derived: set[str] = set()
        for u in unsupported:
            if not u.isdigit():
                continue
            n = int(u)
            if n in products or n in sums:
                derived.add(u)
        return derived

    @staticmethod
    def _extract_derived_numbers(text: str) -> set[str]:
        """Числа, стоящие рядом с арифметическими маркерами — производные
        расчёты модели (не галлюцинация): "677×3=2031", "плюс ещё 5",
        "итого 7809", "скидка 20%" (уже покрыто _extract_percents).
        Маркеры: × * ✕ + − − = «ещё N» «плюс N» «итого N».

        "всего/товаров/позиций/строк" НЕ прощают число сами по
        себе — это слова-описания ("всего 40 товаров"), а не арифметика.
        Число после них прощается только в арифметическом выражении с "="
        или оператором. Иначе "Итого 999 товаров" без 999 в tool_results
        останется HALLUCINATED_NUMBER (false negative-фикс)."""
        derived: set[str] = set()
        s = str(text)
        # Вокруг арифметических операторов/равенства ("677×3=2031",
        # "677×3 позиции = 2031"). Разрешаем слова между операндами и '='.
        arith = re.compile(
            r"\d[\d\s\u00a0]*(?:\s*(?:[×*+−-]|\u00d7|\u2212)\s*[\w\s\u00a0]*\d[\d\s\u00a0]*)+"
            r"\s*[\w\s\u00a0]*=\s*\d+"
        )
        for m in arith.finditer(s):
            for n in re.findall(r"\d+", m.group(0)):
                if len(n) >= 2:
                    derived.add(n)
        # "ещё N", "плюс N", "итого N", "суммарно N" — явный расчёт
        # (сложение/итог). Только эти маркеры прощают число без "=";
        # "всего/товаров/строк/позиций" — нет (см. комментарий выше).
        for m in re.finditer(r"(?:ещё|еще|плюс|итого|суммарно)\s*(?:—|-|:)?\s*(\d+(?:[\s\u00a0]\d{3})*)", s, re.IGNORECASE):
            n = m.group(1).replace("\u00a0", "").replace(" ", "")
            if len(n) >= 2:
                derived.add(n)
        return derived

    @staticmethod
    def _extract_percents(text: str) -> set[str]:
        """Извлечь числа, стоящие рядом с '%' (проценты, вычисленные моделью)."""
        percents: set[str] = set()
        for m in re.finditer(r"(\d{1,3}(?:[\s\u00a0]\d{3})*)\s*%", str(text)):
            percents.add(m.group(1).replace("\u00a0", "").replace(" ", ""))
        # Также числа перед словом "процент"
        for m in re.finditer(r"(\d{1,3})\s*процент", str(text), re.IGNORECASE):
            percents.add(m.group(1))
        return percents

    # ── refusal ────────────────────────────────────────────────────────────

    def _check_refusal(
        self,
        case: TestCase,
        final_text: str,
        reasons: list[str],
    ) -> bool:
        if not case.expect_refusal:
            return True
        final_lower = final_text.lower()
        ok = any(marker in final_lower for marker in REFUSAL_MARKERS)
        if not ok:
            reasons.append("expected refusal markers, answer has none")
        return ok

    # ── value helpers ──────────────────────────────────────────────────────

    def _value_in_text(self, expected: dict[str, Any], text: str, country_aliases: list[str] | None = None) -> bool:
        """Check every key/value from ``expected`` appears in ``text``."""
        text_norm = self._normalize_text(text)
        for key, value in expected.items():
            value_str = str(value).strip()
            if not value_str:
                continue
            # Bool-ключи (available/is_available): семантический матчинг.
            if key in self._BOOL_KEYS and isinstance(value, bool):
                if not self._match_bool(value, text_norm):
                    return False
                continue
            # Страны/города (country): морфология русского языка — «из Германии»
            # ≠ «Германия». Вместо корня из 5 букв (может заматчить «герметик»)
            # используем явные aliases из ground truth (country_aliases), если они
            # заданы; иначе fallback на корень (только для известных длинных слов).
            if key in ("country", "country_of_origin", "city"):
                aliases = country_aliases or []
                if aliases:
                    if not any(a.lower() in text_norm for a in aliases):
                        return False
                    continue
                root = value_str.lower()[:5]
                if len(root) >= 4 and root not in text_norm:
                    return False
                continue
            if key == "count":
                # Word-boundary number match
                if not re.search(rf"(?<!\d){re.escape(value_str)}(?!\d)", text_norm):
                    return False
            elif key == "status":
                # Handled by _check_answer with synonyms — skip here
                continue
            else:
                if value_str.lower() not in text_norm:
                    return False
        return True

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Normalize for matching: lowercase, collapse spaces, strip thousand
        separators inside numbers ("3 064" → "3064", "14 500" → "14500")."""
        s = " ".join(str(text).lower().split())
        # Remove spaces between digits (thousand separators)
        s = re.sub(r"(?<=\d)\s+(?=\d)", "", s)
        return s

    @staticmethod
    def _extract_numbers(text: str) -> list[str]:
        """Extract standalone numbers (prices/counts), not digits inside codes.

        First strips alphanumeric codes (``EXT-01392``, ``АП-100005``) and
        words containing digits, then extracts numeric tokens (2+ digits),
        normalising thousand separators (``14 500`` → ``14500``).
        """
        s = str(text)
        # Remove alphanumeric codes: token with letters+digits, incl. hyphens
        s = re.sub(r"\b[A-Za-zА-Яа-яЁё]+[A-Za-zА-Яа-яЁё0-9-]*[0-9][A-Za-zА-Яа-яЁё0-9-]*\b", " ", s)
        out: list[str] = []
        for token in re.findall(r"[0-9]+(?:[\s\u00a0][0-9]{3})*", s):
            normalized = token.replace("\u00a0", "").replace(" ", "")
            if len(normalized) >= 2:
                out.append(normalized)
        return out
