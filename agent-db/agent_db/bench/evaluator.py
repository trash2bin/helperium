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
"""

from __future__ import annotations

import json
import re
from typing import Any

from .models import EvalResult, RunResult, TestCase

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

        tool_ok = self._check_tool_calls(case, run.tool_calls, reasons)
        entity_name_ok = self._check_entity_names(run.tool_calls, reasons)
        retrieval_ok = self._check_retrieval(case, run.tool_results, reasons)
        answer_ok = self._check_answer(case, run.final_text, reasons)
        hallucination = self._check_hallucination(case, run, reasons)
        refusal_ok = self._check_refusal(case, run.final_text, reasons)

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
        )

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

        # list_ids: require at least N rows across tool results
        if "min_count" in expected:
            return self._check_min_rows(tool_results, int(expected["min_count"]), reasons)

        # Look for expected values in the concatenated tool result text
        for result in tool_results:
            text = str(result.get("result", ""))
            if text and self._value_in_text(expected, text):
                return True

        reasons.append(f"expected {expected} not found in tool results")
        return False

    def _check_min_rows(
        self,
        tool_results: list[dict[str, Any]],
        min_count: int,
        reasons: list[str],
    ) -> bool:
        """True if tool results contain at least ``min_count`` rows."""
        total = 0
        for result in tool_results:
            raw = result.get("result", "")
            n = self._count_rows(raw)
            if n is None:
                # Unknown structure — fall back to non-empty text = 1 row
                if str(raw).strip() not in ("", "[]", "{}", "null"):
                    total += 1
            else:
                total += n
        ok = total >= min_count
        if not ok:
            reasons.append(f"expected >= {min_count} rows in tool results, got {total}")
        return ok

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
        ok = self._value_in_text(expected, final_text)
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

    # ── Фаза 2.5 evaluator-fix: bool semantic matching ───────────────────
    # Бенч: GT {"available": true} ищет literal "true", а модель пишет
    # «в наличии» → false-negative. Bool-ключи матчатся семантически.
    _BOOL_TRUE_MARKERS = [
        "в наличии", "есть в наличии", "доступен", "имеется",
        "на складе", "true", "да", "есть", "1",
    ]
    _BOOL_FALSE_MARKERS = [
        "нет в наличии", "закончил", "не в наличии", "отсутств",
        "нет на складе", "недоступ", "false", "нет", "0",
    ]
    _BOOL_KEYS = {"available", "is_available", "in_stock", "is_active", "active"}

    @staticmethod
    def _match_bool(value: bool, text_norm: str) -> bool:
        """True→«в наличии/доступен», False→«закончился/нет». По подстроке."""
        markers = DeterministicEvaluator._BOOL_TRUE_MARKERS if value else DeterministicEvaluator._BOOL_FALSE_MARKERS
        return any(m in text_norm for m in markers)

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
        # Фаза 2.5: производные числа (суммы "677×3=2031", "плюс ещё 5",
        # "итого 7809") — модель считает их из подтверждённых данных.
        derived = self._extract_derived_numbers(run.final_text)
        unsupported = [n for n in unsupported if n not in derived]
        # Фаза 2.5: числа, выразимые как произведение/сумма подтверждённых
        # (2031 = 677×3, где 677 и 3 в tool_numbers) — производные, не галлюцинация.
        derived_math = self._derive_from_tool_numbers(unsupported, tool_numbers)
        unsupported = [n for n in unsupported if n not in derived_math]
        if unsupported:
            reasons.append(f"unsupported numbers in answer: {unsupported[:5]}")
            return True
        return False

    @staticmethod
    def _derive_from_tool_numbers(unsupported: list[str], tool_numbers: set[str]) -> set[str]:
        """Числа, выразимые как произведение двух подтверждённых (2031 = 677×3)
        или как сумма подмножества подтверждённых (7809 = 677×3+4585+1193+...).
        Ограничено: произведение пары и сумма ≤ 4 слагаемых (перебор мал).
        Однозначные (количества 1..9) не попадают в tool_numbers (_extract_numbers
        фильтрует len>=2) — добавляем их как возможные множители."""
        nums = sorted(int(n) for n in tool_numbers if n.isdigit() and len(n) <= 9)
        # Количества 1..9 как множители (из line-items: price × quantity).
        nums_with_small = set(nums) | {i for i in range(1, 10)}
        nums = sorted(nums_with_small)
        if not nums:
            return set()
        products: set[int] = set()
        for i in range(len(nums)):
            for j in range(len(nums)):
                p = nums[i] * nums[j]
                if p <= 1_000_000:
                    products.add(p)
        # Суммы пар и троек (для "ещё N" и итого).
        sums: set[int] = set()
        for i in range(len(nums)):
            for j in range(i + 1, len(nums)):
                sums.add(nums[i] + nums[j])
        for i in range(len(nums)):
            for j in range(i + 1, len(nums)):
                for k in range(j + 1, len(nums)):
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
        Маркеры: × * ✕ + − - = «ещё N» «плюс N» «итого N» «N позиции»."""
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
        # "ещё N", "плюс N", "итого N", "N позиции", "N строк".
        for m in re.finditer(r"(?:ещё|еще|плюс|итого|всего|суммарно|позиции|позиций|строк|товаров|товара)\s*(?:—|-|:)?\s*(\d+(?:[\s\u00a0]\d{3})*)", s, re.IGNORECASE):
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

    def _value_in_text(self, expected: dict[str, Any], text: str) -> bool:
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
            # ≠ «Германия». Матчим по корню (первые 5 букв, не менее 3).
            if key in ("country", "country_of_origin", "city"):
                root = value_str.lower()[:5]
                if len(root) >= 3 and root not in text_norm:
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
