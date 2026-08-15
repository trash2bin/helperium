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
from .derived_numbers import (
    DerivedNumberDetector,
    extract_percents,
    extract_row_numbers,
    max_row_from_tool_results,
)

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
    "базе нет",
    "нет в каталоге",
    "в каталоге нет",
    "не нашлось",
    "в наличии нет",
    "не значится",
]

# markers of false uncertainty — the agent says "скорее всего" although
# the fact was precisely grounded in tool_results.
UNCERTAINTY_MARKERS = [
    "скорее всего",
    "вероятно",
    "наверное",
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
_DATA_KEYS = (
    "preview",
    "rows",
    "items",
    "data",
    "results",
    "total",
    "returned",
    "count",
)

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

    def __init__(
        self,
        status_synonyms: dict[str, list[str]] | None = None,
        derived_detector: DerivedNumberDetector | None = None,
    ) -> None:
        self.status_synonyms = status_synonyms or DEFAULT_STATUS_SYNONYMS
        self.derived_detector = derived_detector or DerivedNumberDetector()

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

        # Answer completeness: what fraction of expected facts are mentioned?
        answer_completeness = self._check_answer_completeness(case, run.final_text)

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

        refusal_ok = self._check_refusal(
            case, run.final_text, reasons, run.tool_results
        )
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

        # Total mentioned check: for count cases, did agent mention the total?
        total_mentioned = self._check_total_mentioned(case, run, reasons)

        # TOOL_OVERUSE — budget from case
        overuse = self._check_budget(case, run, reasons)
        if overuse:
            error_classes.append(ErrorClass.TOOL_OVERUSE)

        # TOOL_LOOP
        if run.loop_warnings:
            error_classes.append(ErrorClass.TOOL_LOOP)

        # Consistency check: detect contradictions in answer
        # e.g., "Этого товара нет в наличии" but showing price 2200₽
        consistency_ok, consistency_reasons = self._check_consistency(run, reasons)
        if not consistency_ok:
            reasons.extend(consistency_reasons)

        # verdict + error_source
        error_source = self._resolve_error_source(has_tool_error, run)
        verdict = self._compute_verdict(
            case,
            run,
            error_classes,
            has_tool_error,
            tool_ok,
            retrieval_ok,
            answer_ok,
            hallucination,
            refusal_ok,
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
            answer_completeness=answer_completeness,
        )

    # ── verdict / error_source / helpers ────────────────────────────────

    @staticmethod
    def _detect_tool_errors(run: RunResult) -> list[dict[str, Any]]:
        """Return tool-result payloads that are actually error responses.

        Tool errors are distinct from agent errors: the agent may have made a
        correct call, but the service returned ``{"error": "timeout"}``. We
        treat server/transport failures as INFRA_ERROR, not as a wrong answer.
        Client validation responses (HTTP 400/422) describe invalid tool
        arguments selected by the agent, so they are not infrastructure faults.
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
                    if any(
                        marker in stripped.lower()
                        for marker in ("error", "timeout", "failed", "exception")
                    ):
                        errors.append(tr)
                    continue
            else:
                obj = raw
            if isinstance(obj, dict) and any(k in obj for k in _ERROR_KEYS):
                detail = str(obj.get("error", "")).lower()
                if obj.get("ok") is False and (
                    "returned status 400" in detail or "returned status 422" in detail
                ):
                    continue
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
        allow_fuzzy = gt.get("allow_fuzzy_sku", False)
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
            tail = run.final_text[m.end() : m.end() + 60]
            if any(
                mk in tail
                for mk in ("×", "*", "+", "-", "=", "₽", "руб", "итого", "позиц")
            ):
                context_skus.add(m.group(0))
        # Fuzzy SKU matching: if allow_fuzzy_sku is True,
        # consider SKUs that share the same prefix as "supported"
        if allow_fuzzy and answer_skus and tool_skus:
            # Build set of fuzzy-supported SKUs (same prefix match)
            fuzzy_supported = set()
            for ans_sku in answer_skus:
                for tool_sku in tool_skus:
                    ans_parts = ans_sku.rsplit("-", 1)
                    tool_parts = tool_sku.rsplit("-", 1)
                    if len(ans_parts) == 2 and len(tool_parts) == 2:
                        # Same letter prefix + same first digit = supported
                        if (
                            ans_parts[0] == tool_parts[0]
                            and ans_parts[1][0] == tool_parts[1][0]
                        ):
                            fuzzy_supported.add(ans_sku)
                            break
                    elif ans_sku == tool_sku:
                        fuzzy_supported.add(ans_sku)
            # Consider fuzzy-supported SKUs as "allowed" (they're effectively in tools)
            unsupported = (
                (answer_skus - fuzzy_supported) - tool_skus - allowed - context_skus
            )
        else:
            unsupported = answer_skus - tool_skus - allowed - context_skus
        return bool(unsupported)

    # ── consistency check ──────────────────────────────────────────────
    def _check_consistency(
        self, run: RunResult, reasons: list[str]
    ) -> tuple[bool, list[str]]:
        """Detect contradictions in the answer.

        Examples of contradictions:
        - Agent says "нет в наличии" but shows a price
        - Agent says "артикул не найден" but lists a price
        - Mixed availability/status information

        Returns (ok, reasons) where ok=True means no contradictions found.
        """
        reasons_ext = list(reasons)  # copy original reasons
        text_lower = run.final_text.lower()

        # Check for: claimed unavailable but price shown
        has_unavailable = any(
            m in text_lower
            for m in ["нет в наличии", "отсутствует", "недоступен", "недоступно"]
        )
        has_price = bool(re.search(r"\d{2,}\s*(?:₽|руб)", run.final_text))

        if has_unavailable and has_price:
            reasons_ext.append("contradiction: claimed unavailable but showed price")
            return False, reasons_ext

        # Check for: claimed not found but price shown
        has_not_found = any(
            m in text_lower
            for m in ["не найден", "не найдено", "не существует", "нет такого"]
        )
        if has_not_found and has_price:
            reasons_ext.append("contradiction: claimed not found but showed price")
            return False, reasons_ext

        return True, reasons_ext

    # ── LOST_TOTAL ─────────────────────────────────────────────────────

    def _check_lost_total(
        self, case: TestCase, run: RunResult, reasons: list[str]
    ) -> bool:
        """True if the tools returned total:N, the answer omits it and is vague.

        This is the Camry-class defect: agent retrieved 40 but said "много".
        """
        gt = case.ground_truth or {}
        if gt.get("type") == "not_found":
            return False

        # total:N present in tool results?
        # P0 (review-fix): db_map/db_describe могут нести свой total (общее
        # число записей схемы, напр. 407) — брать первый попавшийся total
        # ломает LOST_TOTAL (total_value=407 вместо 74). Если expected.count
        # задан — ищем total, РАВНЫЙ expected.count (это релевантный total);
        # иначе берём первый.
        expected = gt.get("expected", {})
        rules = case.ground_truth.get("answer_rules", {}) or {}
        expected_count = expected.get("count")
        total_value: int | None = None
        totals: list[int] = []
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
                totals.append(int(obj["total"]))
        if expected_count is not None:
            for t in totals:
                if t == int(expected_count):
                    total_value = t
                    break
        else:
            total_value = totals[0] if totals else None
        if total_value is None:
            return False

        # Must the answer mention the total? Only if expected or answer_rules say so.
        require_total = rules.get("expect_total_mentioned", False) or (
            isinstance(expected_count, (int, float))
            and int(expected_count) == total_value
        )
        if not require_total:
            return False

        text_norm = self._normalize_text(run.final_text)
        # total mentioned only as a standalone number, not inside a code
        # like "V40" (Camry V40) or "АП-100005" — otherwise "40" in "v40"
        # would suppress LOST_TOTAL (false negative for the Camry class).
        total_mentioned = any(
            n == str(total_value)
            for n in self._extract_numbers(run.final_text, include_single_digit=True)
        )
        if total_mentioned:
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

    def _check_false_uncertainty(
        self, case: TestCase, run: RunResult, reasons: list[str]
    ) -> bool:
        """True if an uncertainty marker sits next to a fact that was grounded.

        Detector: find uncertainty markers ("скорее всего", "вероятно"...) in
        the answer, then check whether the fact near the marker is supported by
        tool_results. Only flags when a *grounded* fact is hedged.
        """
        text_lower = run.final_text.lower()
        if not any(
            re.search(rf"(?<!\w){re.escape(m)}(?!\w)", text_lower)
            for m in UNCERTAINTY_MARKERS
        ):
            return False

        # Skip if the case is inherently uncertain (e.g. open question)
        if case.ground_truth.get("type") == "not_found":
            return False

        # The fact is grounded if retrieval found expected atom
        if not self._retrieval_has_ground_truth(case, run):
            return False

        # We have a grounded fact + uncertainty marker → false uncertainty
        reasons.append(
            "FALSE_UNCERTAINTY: grounded fact hedged with uncertainty marker"
        )
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
            reasons.append(
                f"TOOL_OVERUSE: {tool_calls} tool calls > {budget['max_tool_calls']}"
            )
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
            reasons.append(
                f"TOOL_OVERUSE: cost ${cost:.4f} > ${budget['max_cost_usd']}"
            )
            violated = True
        return violated

    def _check_total_mentioned(
        self,
        case: TestCase,
        run: RunResult,
        reasons: list[str],
    ) -> bool:
        """For count cases: did agent mention the total number?

        This complements LOST_TOTAL: LOST_TOTAL checks if total is known but
        not mentioned as a specific number. This checks if total is mentioned
        at all (even vaguely).
        """
        gt = case.ground_truth or {}
        if gt.get("type") != "count":
            return True

        # Extract total from tool results
        total = None
        for tr in run.tool_results:
            raw = tr.get("result", "")
            if isinstance(raw, str):
                try:
                    obj = json.loads(raw)
                    if "total" in obj:
                        total = obj["total"]
                        break
                except (json.JSONDecodeError, ValueError):
                    pass
            else:
                if isinstance(obj := raw, dict) and "total" in obj:
                    total = obj["total"]
                    break

        if total is None:
            return True  # No total in tools → nothing to check

        total_str = str(total)
        # Check if total is mentioned in answer
        if total_str in self._extract_numbers(
            run.final_text, include_single_digit=True
        ):
            return True  # Total mentioned explicitly

        # Also check for vague mentions ("всего N", "N штук" etc.)
        text_norm = self._normalize_text(run.final_text)
        vague_markers = ["всего", "на", "штук", "позиций"]
        has_vague_total = any(m in text_norm for m in vague_markers)

        if has_vague_total and total_str in self._extract_numbers(
            run.final_text.replace(" ", ""), include_single_digit=True
        ):
            return True

        reasons.append(f"total {total} not mentioned in answer")
        return False

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
                reasons.append(
                    f"invalid entity name: {entity!r} (valid: {sorted(VALID_ENTITIES)[:3]}...)"
                )
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
                reasons.append(
                    f"expected tool in {must_any}, called {sorted(called_names)}"
                )
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
            return self._check_min_rows(
                tool_results, int(expected["min_count"]), reasons
            )

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
            reasons.append(
                f"expected >= {min_count} rows in tool results, got {total} (unique)"
            )
        return ok

    def _extract_row_identities(
        self, raw: Any, entity: str = ""
    ) -> list[tuple[str, Any]] | None:
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
                    if (
                        key in obj
                        and isinstance(obj[key], (int, float))
                        and obj[key] > 0
                    ):
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
                reasons.append(
                    f"status '{status}' (synonyms {syns.get(status)}) not in answer"
                )
            return ok

        # Exact value / count — direct substring match
        ok = self._value_in_text(
            expected,
            final_text,
            country_aliases=gt.get("country_aliases", []),
            value_aliases=gt.get("value_aliases", {}),
        )
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

    def _check_answer_completeness(
        self,
        case: TestCase,
        final_text: str,
    ) -> float:
        """Returns 0.0-1.0: what fraction of expected facts are mentioned.

        Helps differentiate "показал 2 из 4 товаров" vs "показал 0 из 4".
        """
        expected = (case.ground_truth or {}).get("expected", {})
        if not expected:
            return 1.0
        if "min_count" in expected:
            # list_ids: empty answer = 0.0, non-empty = 1.0
            return 1.0 if (final_text and final_text.strip()) else 0.0

        mentioned = 0
        total = 0
        for key, value in expected.items():
            if key == "status":
                # Handled via synonyms by _check_answer; skip here
                total += 1
                mentioned += 1
                continue
            total += 1
            if self._value_in_text(
                {key: value},
                final_text,
                value_aliases=(case.ground_truth or {}).get("value_aliases", {}),
            ):
                mentioned += 1

        return mentioned / total if total > 0 else 1.0

    # ── bool semantic matching ───────────────────
    # Бенч: GT {"available": true} ищет literal "true", а модель пишет
    # «в наличии» → false-negative. Bool-ключи матчатся семантически.
    # убраны слабые маркеры (да/есть/нет/1/0) — слишком широкие (false positives).
    _BOOL_TRUE_MARKERS = [
        "в наличии",
        "есть в наличии",
        "имеется в наличии",
        "доступен",
        "доступно",
        "на складе",
        "в наличии на складе",
        "true",
    ]
    _BOOL_FALSE_MARKERS = [
        "нет в наличии",
        "отсутствует",
        "недоступен",
        "недоступно",
        "закончился",
        "закончилась",
        "нет на складе",
        "снят с продажи",
        "не в наличии",
        "false",
    ]
    _BOOL_KEYS = {"available", "is_available", "in_stock", "is_active", "active"}

    @staticmethod
    def _match_bool(value: bool, text_norm: str, key: str | None = None) -> bool:
        """True→«в наличии/доступен», False→«закончился/нет». По подстроке.
        негативные маркеры проверяются ПЕРВЫМИ для обоих значений —
                «нет в наличии» содержит «в наличии», и для value=True это не должно
                матчиться. Если найден сильный негатив → ответ = False, независимо
                от value.
        """
        # JSON-вид: "is_available": true — матчить конкретный ключ.
        # P0 (review-fix): JSON-tool results содержат несколько bool-полей
        # (is_available=true, is_bestseller=false, ...) — глобальный поиск
        # `false` ломал retrieval для available=true.
        if key:
            pat = re.compile(rf'"?{re.escape(key)}"?\s*:\s*(true|false)')
            m = pat.search(text_norm)
            if m:
                return m.group(1) == str(value).lower()
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
            if not self._check_refusal(case, run.final_text, [], run.tool_results):
                reasons.append("hallucination: absence case invented data (no refusal)")
                return True
            forbidden = gt.get("forbidden", {})
            if forbidden and self._value_in_text(forbidden, run.final_text):
                reasons.append(
                    "hallucination: absence case answered with forbidden data"
                )
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
            n
            for n in answer_numbers
            if n not in tool_numbers and n not in question_numbers
        ]
        # Проценты ("скидка ~20%", "-30% к цене") — производные расчёты модели
        # от подтверждённых цен (old_price→price). Не галлюцинация.
        percent_numbers = extract_percents(run.final_text)
        unsupported = [n for n in unsupported if n not in percent_numbers]
        # : производные числа из арифметического контекста ответа
        # ("677×3=2031", "плюс ещё 5", "итого 7809")
        derived_text = self.derived_detector.find_derived_from_text(run.final_text)
        unsupported = [n for n in unsupported if n not in derived_text]
        # : числа, выразимые как произведение/сумма подтверждённых
        # (2031 = 677×3, где 677 и 3 в tool_numbers) — производные, не галлюцинация.
        derived_math = self.derived_detector.find_derived_from_tool_numbers(
            unsupported, tool_numbers
        )
        unsupported = [n for n in unsupported if n not in derived_math]
        # : номера строк списка/таблицы (1..25 в начале markdown-строки:
        # "| 12 | Товар", "- 10.", "1. Товар") — это нумерация, не факты.
        # Row numbers bound derived from actual tool_results (preview/total),
        # not hardcoded 50 — avoids false positives on long lists.
        max_row = max_row_from_tool_results(run.tool_results)
        row_numbers = extract_row_numbers(run.final_text, max_row)
        unsupported = [n for n in unsupported if n not in row_numbers]
        # Breakdown-числа: агент подтвердил total (74) и разбил по категориям
        # (свечи 12, колодки 12, ...). Разрешаем ТОЛЬКО если кейс явно
        # разрешает breakdown через ground_truth.breakdown_allowed=true.
        # Без флага — числа в breakdown считаются галлюцинацией, так как
        # глобальная эвристика sum<=total позволяет выдумать плейзибл breakdown
        # с нулевой связью к tool_results.
        confirmed_total = self._confirmed_total(run)
        gt = case.ground_truth or {}
        breakdown_allowed = gt.get("breakdown_allowed", False)
        if breakdown_allowed and confirmed_total is not None and unsupported:
            # breakdown-числа — это НЕ сам total (total уже подтверждён и
            # исключён из unsupported на этапе tool_numbers). Считаем только
            # неподтверждённые числа < total, исключая сам total.
            small = [
                int(n) for n in unsupported if n.isdigit() and int(n) < confirmed_total
            ]
            if small and sum(small) <= confirmed_total:
                # ответ содержит сам total (главное число подтверждено)?
                if str(confirmed_total) in set(self._extract_numbers(run.final_text)):
                    unsupported = [
                        n
                        for n in unsupported
                        if not (n.isdigit() and int(n) < confirmed_total)
                    ]
        if unsupported:
            reasons.append(f"unsupported numbers in answer: {unsupported[:5]}")
            return True
        return False

    @staticmethod
    def _confirmed_total(run: RunResult) -> int | None:
        """Наибольший total в tool_results — подтверждённое число записей."""
        best: int | None = None
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
                t = int(obj["total"])
                best = t if best is None else max(best, t)
        return best

    # ── refusal ────────────────────────────────────────────────────────────

    def _check_refusal(
        self,
        case: TestCase,
        final_text: str,
        reasons: list[str],
        tool_results: list[dict[str, Any]] | None = None,
    ) -> bool:
        if not case.expect_refusal:
            return True
        # Primary signal: structural evidence — tool_results are empty/empty_hint
        # AND answer doesn't contain data-like content (numbers, availability).
        # This is more reliable than text markers (whack-a-mole).
        if tool_results is not None:
            empty_results = self._check_empty_results(tool_results)
            if empty_results:
                # Also check no expected facts are hallucinated in answer
                gt = case.ground_truth or {}
                forbidden = gt.get("forbidden", {})
                # If forbidden is empty, no facts to check
                # But also check answer doesn't contain data-like content
                has_data = self._answer_looks_like_data(final_text)
                if (
                    not forbidden or not self._value_in_text(forbidden, final_text)
                ) and not has_data:
                    return True  # Structural refusal confirmed
        # Fallback: text markers (kept for backwards compat)
        final_lower = final_text.lower()
        ok = any(marker in final_lower for marker in REFUSAL_MARKERS)
        if not ok:
            reasons.append("expected refusal markers, answer has none")
        return ok

    @staticmethod
    def _answer_looks_like_data(text: str) -> bool:
        """Heuristic: does the answer look like it's providing data (not a refusal)?
        Checks for numbers (prices, counts) or strong availability assertions."""
        t = text.lower()
        # Numbers that look like prices/quantities (2+ digits)
        import re

        if re.search(r"\b\d{2,}\b", text):
            return True
        # Strong availability/presence assertions
        data_markers = [
            "в наличии",
            "есть в наличии",
            "имеется",
            "доступен",
            "цена",
            "стоит",
            "руб",
            "₽",
            "да, есть",
            "да, в наличии",
        ]
        if any(m in t for m in data_markers):
            return True
        return False

    # ── value helpers ──────────────────────────────────────────────────────

    def _value_in_text(
        self,
        expected: dict[str, Any],
        text: str,
        country_aliases: list[str] | None = None,
        value_aliases: dict[str, dict[str, list[str]]] | None = None,
    ) -> bool:
        """Check every expected key/value with explicitly fixture-scoped aliases."""
        text_norm = self._normalize_text(text)
        aliases_by_key = value_aliases or {}
        for key, value in expected.items():
            aliases = aliases_by_key.get(key, {}).get(str(value), [])
            if not self._match_key_value(
                key,
                value,
                text_norm,
                country_aliases,
                aliases,
            ):
                return False
        return True

    def _match_key_value(
        self,
        key: str,
        value: Any,
        text_norm: str,
        country_aliases: list[str] | None = None,
        value_aliases: list[str] | None = None,
    ) -> bool:
        """Match a single key/value pair against normalized text."""
        value_str = str(value).strip()
        if not value_str:
            return True

        # Bool-ключи (available/is_available): семантический матчинг.
        if key in self._BOOL_KEYS and isinstance(value, bool):
            return self._match_bool_value(value, text_norm, key)

        # Страны/города (country): морфология русского языка.
        if key in ("country", "country_of_origin", "city"):
            return self._match_location(value_str, text_norm, country_aliases)

        if key == "count":
            return self._match_count(value_str, text_norm)

        if key == "status":
            # Handled by _check_answer with synonyms — skip here
            return True

        if value_aliases:
            return self._match_explicit_value_aliases(
                value_str, value_aliases, text_norm
            )

        # Plain text substring match
        return self._match_plain_text(value_str, text_norm)

    def _match_explicit_value_aliases(
        self,
        value_str: str,
        aliases: list[str],
        text_norm: str,
    ) -> bool:
        """Match fixture-listed display aliases while rejecting explicit negation.

        Aliases are case-local ground truth, not a generic fuzzy matcher. The
        expected raw value remains accepted, and a phrase preceded by ``не`` or
        ``без`` (with up to two intervening words) does not satisfy the fact.
        """
        candidates = [value_str, *aliases]
        for candidate in candidates:
            candidate_norm = self._normalize_text(candidate)
            if not candidate_norm:
                continue
            pattern = re.compile(
                rf"(?<!\w){re.escape(candidate_norm)}(?!\w)"
            )
            for match in pattern.finditer(text_norm):
                prefix = text_norm[max(0, match.start() - 48) : match.start()]
                negated = re.search(
                    r"(?:^|\s)(?:не|без)(?:\s+\w+){0,2}\s*$", prefix
                )
                if not negated:
                    return True
        return False

    def _match_bool_value(
        self, value: bool, text_norm: str, key: str | None = None
    ) -> bool:
        """Match boolean value semantically: true→'в наличии', false→'нет в наличии'."""
        return self._match_bool(value, text_norm, key=key)

    def _match_location(
        self,
        value_str: str,
        text_norm: str,
        country_aliases: list[str] | None = None,
    ) -> bool:
        """Match country/city with aliases or morphological root fallback."""
        aliases = country_aliases or []
        if aliases:
            return any(a.lower() in text_norm for a in aliases)
        root = value_str.lower()[:5]
        return not (len(root) >= 4 and root not in text_norm)

    def _match_count(self, value_str: str, text_norm: str) -> bool:
        """Word-boundary number match for count."""
        return bool(re.search(rf"(?<!\d){re.escape(value_str)}(?!\d)", text_norm))

    def _match_plain_text(self, value_str: str, text_norm: str) -> bool:
        """Simple case-insensitive substring match."""
        return value_str.lower() in text_norm

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Normalize for matching: lowercase, collapse spaces, strip thousand
        separators inside numbers ("3 064" → "3064", "14 500" → "14500")."""
        s = " ".join(str(text).lower().split())
        # Remove spaces between digits (thousand separators)
        s = re.sub(r"(?<=\d)\s+(?=\d)", "", s)
        return s

    @staticmethod
    def _extract_numbers(text: str, include_single_digit: bool = False) -> list[str]:
        """Extract standalone numbers (prices/counts), not digits inside codes.

        First strips alphanumeric codes (``EXT-01392``, ``АП-100005``) and
        words containing digits, then extracts numeric tokens (two or more digits by default; one digit when requested),
        normalising ordinary, non-breaking and narrow non-breaking thousand separators (``14 500`` → ``14500``).
        """
        s = str(text)
        # Remove alphanumeric codes: token with letters+digits, incl. hyphens
        s = re.sub(
            r"\b[A-Za-zА-Яа-яЁё]+[A-Za-zА-Яа-яЁё0-9-]*[0-9][A-Za-zА-Яа-яЁё0-9-]*\b",
            " ",
            s,
        )
        out: list[str] = []
        for token in re.findall(r"[0-9]+(?:[\s\u00a0][0-9]{3})*", s):
            normalized = (
                token.replace("\u00a0", "")
                .replace("\u202f", "")
                .replace("\u2009", "")
                .replace(" ", "")
            )
            if len(normalized) >= 2 or include_single_digit:
                out.append(normalized)
        return out
