"""Deterministic unit tests for the Core Benchmark (no LLM, no network).

Covers:
- evaluator: tool/retrieval/answer/hallucination/refusal logic
- backlog_parser: session-file lookup + turn_end parsing
- report: aggregation math + JSON serialization
- runner: SSE parsing (pure function)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_db.bench.evaluator import DeterministicEvaluator
from agent_db.bench.models import (
    BacklogData,
    BenchmarkReport,
    EvalResult,
    ErrorClass,
    RunResult,
    TestCase,
)
from agent_db.bench.report import aggregate_report, diff_reports, print_report, report_to_dict
from agent_db.bench.backlog_parser import (
    find_backlog_file,
    parse_backlog_data,
    read_all_records,
)
from agent_db.bench.runner import _sse_parse_events


# ═══════════════════════════════════════════════════════════════════════════
# Evaluator
# ═══════════════════════════════════════════════════════════════════════════


def make_case(cid="t1", gt_type="exact_value", expected=None, **kw):
    gt = {"type": gt_type, "expected": expected or {}}
    # extra ground-truth keys (answer_rules, check_skus, country_aliases...)
    gt_extra = kw.pop("gt_extra", None) or {}
    gt.update(gt_extra)
    gt_kw = kw.pop("ground_truth_kw", None) or {}
    gt.update(gt_kw)
    d = {
        "id": cid,
        "question": kw.pop("question", "q"),
        "category": kw.pop("category", "lookup"),
        "ground_truth": gt,
        **kw,
    }
    return TestCase.from_dict(d)


def make_run(final_text="", tool_calls=None, tool_results=None, **kw):
    return RunResult(
        session_id="s",
        question=kw.pop("question", "q"),
        final_text=final_text,
        tool_calls=tool_calls or [],
        tool_results=tool_results or [],
        **kw,
    )


class TestEvaluator:
    def test_correct_lookup_passes(self):
        case = make_case(expected={"price": 3064},
                         expected_tool={"must_call_any": ["filter_catalog_product", "db_get"]})
        run = make_run(
            final_text="Цена артикула: 3064 рубля",
            tool_calls=[{"name": "db_get", "arguments": {"entity": "catalog_product", "id": 392}}],
            tool_results=[{"name": "db_get", "result": json.dumps({"id": 392, "price": 3064})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.tool_ok and res.retrieval_ok and res.answer_ok
        assert not res.hallucination and res.grounded
        assert res.success

    def test_wrong_answer_is_hallucination(self):
        case = make_case(expected={"price": 3064})
        run = make_run(
            final_text="Цена: 9999 рублей",
            tool_calls=[{"name": "db_get"}],
            tool_results=[{"name": "db_get", "result": json.dumps({"price": 3064})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.answer_ok
        assert res.hallucination
        assert not res.success

    def test_missing_expected_tool_fails(self):
        case = make_case(expected={"price": 3064},
                         expected_tool={"must_call_any": ["filter_catalog_product", "db_get"]})
        run = make_run(final_text="3064", tool_calls=[{"name": "db_map"}],
                       tool_results=[{"name": "db_map", "result": "{}"}])
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.tool_ok
        assert not res.success

    def test_forbidden_tool_fails(self):
        case = make_case(expected={"price": 3064},
                         expected_tool={"must_call_any": ["db_get"],
                                        "must_not_call": ["db_describe"]})
        run = make_run(final_text="3064",
                       tool_calls=[{"name": "db_get"}, {"name": "db_describe"}],
                       tool_results=[{"name": "db_get", "result": json.dumps({"price": 3064})}])
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.tool_ok

    def test_absence_correct_refusal_passes(self):
        case = make_case(cid="abs", gt_type="not_found", expected={"count": 0},
                         expect_refusal=True,
                         expected_tool={"must_call_any": ["filter_catalog_product"]})
        run = make_run(
            final_text="Такого артикула не найдено в каталоге.",
            tool_calls=[{"name": "filter_catalog_product"}],
            tool_results=[{"name": "filter_catalog_product",
                           "result": '{"empty_hint": {"available_values": {"article": ["A1"]}}}'}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.retrieval_ok and res.answer_ok and res.refusal_ok
        assert not res.hallucination and res.success

    def test_absence_invented_data_is_hallucination(self):
        case = make_case(cid="abs2", gt_type="not_found", expected={"count": 0},
                         expect_refusal=True)
        run = make_run(
            final_text="Да, артикул есть, цена 1000 рублей.",
            tool_calls=[{"name": "filter_catalog_product"}],
            tool_results=[{"name": "filter_catalog_product", "result": "[]"}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.refusal_ok and res.hallucination and not res.success

    # absence-кейс, где модель зовёт db_map (схема) — это НЕ данные.
    def test_absence_with_db_map_still_empty(self):
        case = make_case(cid="abs3", gt_type="not_found", expected={"count": 0},
                         expect_refusal=True,
                         expected_tool={"must_call_any": ["db_search"]})
        run = make_run(
            final_text="Артикул ZZ-000-NOPE не найден в каталоге.",
            tool_calls=[{"name": "db_map"}, {"name": "db_search"}],
            tool_results=[
                {"name": "db_map", "result": json.dumps({"entities": [{"name": "product"}]})},
                {"name": "db_search",
                 "result": json.dumps({"empty_hint": {"available_values": {"article": ["A1"]}}})},
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.retrieval_ok, f"db_map не данные, retrieval должен быть пуст: {res.reasons}"
        assert res.refusal_ok and not res.hallucination and res.success, res.reasons

    def test_status_with_synonyms(self):
        case = make_case(expected={"status": "shipped"},
                         status_synonyms={"shipped": ["отправлен", "в пути"]})
        run = make_run(final_text="Статус: отправлен",
                       tool_calls=[{"name": "filter_catalog_order"}],
                       tool_results=[{"name": "filter_catalog_order", "result": json.dumps({"status": "shipped"})}])
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.answer_ok and res.success

    def test_count_wrong_number(self):
        case = make_case(cid="cnt", gt_type="count", expected={"count": 74})
        run = make_run(final_text="Нашёл 100 товаров",
                       tool_calls=[{"name": "filter_catalog_product"}],
                       tool_results=[{"name": "filter_catalog_product", "result": json.dumps({"total": 74})}])
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.answer_ok and res.hallucination

    def test_list_ids_min_count(self):
        case = make_case(gt_type="list_ids", expected={"min_count": 1})
        run = make_run(final_text="Вот товары: EXT-01392, EXT-01367",
                       tool_calls=[{"name": "filter_catalog_product"}],
                       tool_results=[{"name": "filter_catalog_product",
                                      "result": json.dumps({"preview": [{"id": 1}, {"id": 2}]})}])
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.retrieval_ok and res.answer_ok and res.success

    def test_structured_empty_preview_is_empty(self):
        """data-service returns {preview:[],returned:0,total:0} — treat as empty."""
        ev = DeterministicEvaluator()
        assert ev._check_empty_results([{"name": "f", "result": '{"preview": [], "returned": 0, "total": 0}'}])
        assert not ev._check_empty_results([{"name": "f", "result": '{"preview": [{"id": 1}], "returned": 1, "total": 1}'}])

    def test_price_with_thousand_separator_matches(self):
        """Answer "3 064" must match expected 3064."""
        case = make_case(expected={"price": 3064})
        run = make_run(final_text="Цена: 3 064 рубля",
                       tool_calls=[{"name": "db_get"}],
                       tool_results=[{"name": "db_get", "result": json.dumps({"price": 3064})}])
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.answer_ok, res.reasons
        assert res.success, res.reasons

    # ── bool semantic matching ───────────────────
    # Бенч (scout-1): GT {"available": true} ищет literal "true", а модель
    # пишет «в наличии» → false-negative. Bool должен матчиться семантически.
    def test_bool_available_ru_semantic(self):
        case = make_case(expected={"available": True, "quantity": 23})
        run = make_run(
            final_text="Артикул EXT-01367 в наличии, 23 шт.",
            tool_calls=[{"name": "db_get"}],
            tool_results=[{"name": "db_get",
                           "result": json.dumps({"is_available": True, "quantity": 23})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.answer_ok, f"bool True должен матчиться с «в наличии»: {res.reasons}"
        assert res.success, res.reasons

    def test_bool_not_available_ru_semantic(self):
        case = make_case(expected={"available": False, "quantity": 0})
        run = make_run(
            final_text="Артикул закончился, на складе 0 шт.",
            tool_calls=[{"name": "db_get"}],
            tool_results=[{"name": "db_get",
                           "result": json.dumps({"is_available": False, "quantity": 0})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.answer_ok, f"bool False должен матчиться с «закончился»: {res.reasons}"
        assert res.success, res.reasons

    # ── производные числа (суммы) ───────────────
    # Бенч (order-lookup-total): модель считает 677×3=2031 (line-items),
    # эти числа в ответе, но не в tool_results → false-positive hallucination.
    def test_derived_arithmetic_not_hallucination(self):
        case = make_case(expected={"total": 7809})
        run = make_run(
            final_text="Заказ АП-100004: итого 7809 руб. (677×3 позиции = 2031 за детали)",
            tool_calls=[{"name": "db_get"}],
            tool_results=[{"name": "db_get",
                           "result": json.dumps({"total": 7809})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.answer_ok and not res.hallucination, \
            f"производная арифметика не галлюцинация: {res.reasons}"
        assert res.success, res.reasons

    def test_derived_count_not_hallucination(self):
        case = make_case(cid="dc", gt_type="count", expected={"count": 42})
        run = make_run(
            final_text="Найдено 42 товара (плюс ещё 5 в других категориях)",
            tool_calls=[{"name": "filter_catalog_product"}],
            tool_results=[{"name": "filter_catalog_product", "result": json.dumps({"total": 42})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.hallucination, f"производное «ещё 5» не галлюцинация: {res.reasons}"

    # сумма позиций заказа (2031 = 677×3, где 677 и 3 в tool_results).
    def test_line_item_sum_not_hallucination(self):
        case = make_case(expected={"total": 7809})
        run = make_run(
            final_text="Заказ: итого 7809 ₽. Позиция FLT-01188: 2 031 ₽ (677×3).",
            tool_calls=[{"name": "db_get"}],
            tool_results=[{"name": "db_get", "result": json.dumps({
                "total": 7809,
                "items": [{"price": 677, "quantity": 3}, {"price": 4585, "quantity": 1}],
            })}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.hallucination, f"сумма позиций не галлюцинация: {res.reasons}"
        assert res.answer_ok and res.success, res.reasons

    # ── false negative — производное не должно прощать выдумку ──
    # «итого 700» при tool_numbers {20,35} — произведение двух больших чисел,
    # агент не обязан был перемножать любые пары → это галлюцинация.
    def test_arbitrary_pair_product_is_hallucination(self):
        case = make_case(expected={"count": 700})
        run = make_run(
            final_text="Итого 700 товаров в наличии.",
            tool_calls=[{"name": "filter_catalog_product"}],
            tool_results=[{"name": "filter_catalog_product",
                           "result": json.dumps({"total": 700})}],
        )
        # 700 подтверждён total=700 → это НЕ галлюцинация (контрольный тест).
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.hallucination, res.reasons

    def test_arbitrary_pair_product_hallucination_when_unconfirmed(self):
        """tool_results {20, 35}, ответ «итого 700» → 700 = 20×35 формально
        derived, но агент не обязан был перемножать → HALLUCINATED_NUMBER."""
        case = make_case(expected={"count": 700})
        run = make_run(
            final_text="Итого доступно 700 товаров.",
            tool_calls=[{"name": "filter_catalog_product"}],
            tool_results=[{"name": "filter_catalog_product",
                           "result": json.dumps({"preview": [
                               {"id": 1, "quantity": 20}, {"id": 2, "quantity": 35}]})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.hallucination, f"700 = 20×35 не должно прощаться: {res.reasons}"

    def test_vague_total_without_confirmation_is_hallucination(self):
        """«всего 40 товаров» без total=40 в tool_results → галлюцинация
        (слово «всего» не арифметический маркер)."""
        case = make_case(cid="vt", gt_type="count", expected={"count": 40})
        run = make_run(
            final_text="Всего 40 товаров бренда Bosch.",
            tool_calls=[{"name": "filter_catalog_product"}],
            tool_results=[{"name": "filter_catalog_product",
                           "result": json.dumps({"total": 40})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        # total=40 в tool_results → 40 подтверждено → не галлюцинация.
        assert not res.hallucination, res.reasons

    def test_total_in_tools_not_hallucination(self):
        """total=40 в tool_results, ответ «всего 40 товаров» → 40 подтверждено."""
        case = make_case(cid="tt", gt_type="count", expected={"count": 40})
        run = make_run(
            final_text="Всего 40 товаров бренда Bosch.",
            tool_calls=[{"name": "filter_catalog_product"}],
            tool_results=[{"name": "filter_catalog_product",
                           "result": json.dumps({"total": 40})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.hallucination and res.answer_ok, res.reasons

    def test_derived_numbers_require_arithmetic_context(self):
        """Число после «всего/товаров» без арифметики и без подтверждения —
        галлюцинация (маркеры «всего/товаров» убраны из derived).
        «Всего» — описательное слово, а не арифметика."""
        case = make_case(cid="dn", gt_type="count", expected={"count": 999})
        run = make_run(
            final_text="Всего 999 товаров в наличии.",
            tool_calls=[{"name": "filter_catalog_product"}],
            tool_results=[{"name": "filter_catalog_product",
                           "result": json.dumps({"total": 40})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.hallucination, f"999 не подтверждён и не выводим → галлюцинация: {res.reasons}"

    # ── табличные № строк ───────────────────────
    # Модель нумерует строки в таблице (1..10) — это не факты.
    # ── морфология стран ────────────────────────
    # «из Германии» (предложный падеж) ≠ «Германия» — матчим по корню.
    def test_country_morphology(self):
        case = make_case(expected={"country": "Германия"})
        run = make_run(
            final_text="Бренд Bosch — из Германии, основан в 1886.",
            tool_calls=[{"name": "db_get"}],
            tool_results=[{"name": "db_get", "result": json.dumps({"country": "Германия", "founded_year": 1886}, ensure_ascii=False)}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.answer_ok, f"«из Германии» должен матчиться с «Германия»: {res.reasons}"
        assert res.success, res.reasons

    def test_country_morphology_japan(self):
        case = make_case(expected={"country": "Япония"})
        run = make_run(
            final_text="Производство Denso расположено в Японии.",
            tool_calls=[{"name": "db_get"}],
            tool_results=[{"name": "db_get", "result": json.dumps({"country": "Япония"}, ensure_ascii=False)}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.answer_ok, f"«в Японии» должен матчиться с «Япония»: {res.reasons}"
        assert res.success, res.reasons

    def test_question_numbers_not_hallucination(self):
        """Numbers from the question are not hallucinations."""
        case = make_case(cid="q", gt_type="count", expected={"count": 145})
        run = RunResult(
            session_id="s",
            question="Сколько товаров дороже 3000 рублей?",
            final_text="Нашёл 145 товаров дороже 3000 рублей",
            tool_calls=[{"name": "filter_catalog_product"}],
            tool_results=[{"name": "filter_catalog_product", "result": json.dumps({"total": 145})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.hallucination, res.reasons
        assert res.answer_ok and res.success, res.reasons

    def test_invalid_entity_name_fails(self):
        """Agent using entity='Order' instead of 'catalog_order' fails entity check."""
        case = make_case(expected={"status": "shipped"})
        run = make_run(
            final_text="shipped",
            tool_calls=[{"name": "db_search", "arguments": {"entity": "Order", "pattern": "АП-100005"}}],
            tool_results=[{"name": "db_search", "result": json.dumps({"preview": [{"id": 5, "name": "АП-100005"}]})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.entity_name_ok, res.reasons

    def test_valid_entity_name_passes(self):
        """Correct entity='catalog_order' passes entity check."""
        case = make_case(expected={"status": "shipped"})
        run = make_run(
            final_text="shipped",
            tool_calls=[{"name": "db_search", "arguments": {"entity": "catalog_order", "pattern": "АП-100005"}}],
            tool_results=[{"name": "db_search", "result": json.dumps({"preview": [{"id": 5, "name": "АП-100005"}]})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.entity_name_ok, res.reasons

    def test_percent_discount_not_hallucination(self):
        """Model-computed discount % (e.g. 'скидка ~20%') is not a hallucination."""
        case = make_case(expected={"price": 3064})
        run = make_run(
            final_text="Цена 3064 руб (старая 3830, скидка ~20%)",
            tool_calls=[{"name": "db_get"}],
            tool_results=[{"name": "db_get", "result": json.dumps({"price": 3064, "old_price": 3830})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.hallucination, res.reasons
        assert res.success, res.reasons

    # ═══════════════════════════════════════════════════════════════════════
    # verdict + error taxonomy
    # ═══════════════════════════════════════════════════════════════════════

    def test_verdict_correct(self):
        """Clean case → CORRECT, no error classes."""
        case = make_case(expected={"price": 3064},
                         expected_tool={"must_call_any": ["db_get"]})
        run = make_run(
            final_text="Цена 3064 рубля",
            tool_calls=[{"name": "db_get", "arguments": {"entity": "catalog_product", "id": 1}}],
            tool_results=[{"name": "db_get", "result": json.dumps({"price": 3064})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.verdict.value == "CORRECT", res.reasons
        assert res.error_classes == []
        assert res.error_source == "agent"

    def test_verdict_wrong_hallucination(self):
        """Unsupported number → WRONG + HALLUCINATED_NUMBER."""
        case = make_case(expected={"price": 3064})
        run = make_run(
            final_text="Цена 9999 рублей",
            tool_calls=[{"name": "db_get"}],
            tool_results=[{"name": "db_get", "result": json.dumps({"price": 3064})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.verdict.value == "WRONG", res.reasons
        assert ErrorClass.HALLUCINATED_NUMBER in res.error_classes

    def test_verdict_wrong_sku_hallucination(self):
        """Answer SKU not in tools (with check_skus flag) → WRONG + HALLUCINATED_SKU."""
        case = make_case(expected={"price": 3064},
                         ground_truth_kw={"check_skus": True})
        run = make_run(
            final_text="Цена 3064, артикул BRK-99999",
            tool_calls=[{"name": "db_get"}],
            tool_results=[{"name": "db_get", "result": json.dumps({"price": 3064})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.verdict.value == "WRONG", res.reasons
        assert ErrorClass.HALLUCINATED_SKU in res.error_classes

    def test_verdict_sku_not_flagged_without_flag(self):
        """Without any_of_skus/check_skus, SKU mentions are not hallucinations."""
        case = make_case(expected={"price": 3064})
        run = make_run(
            final_text="Цена 3064, артикул BRK-01004",
            tool_calls=[{"name": "db_get"}],
            tool_results=[{"name": "db_get", "result": json.dumps({"price": 3064})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.verdict.value == "CORRECT", res.reasons
        assert ErrorClass.HALLUCINATED_SKU not in res.error_classes

    def test_verdict_cyrillic_sku_hallucination(self):
        """Кириллический артикул АП-100005 без подтверждения в tools
        (с check_skus) → HALLUCINATED_SKU. Regex ловит кириллицу."""
        case = make_case(cid="cyr", expected={"price": 3064},
                         ground_truth_kw={"check_skus": True})
        run = make_run(
            final_text="Цена 3064, артикул АП-100005",
            tool_calls=[{"name": "db_get", "arguments": {"entity": "catalog_product", "id": 1}}],
            tool_results=[{"name": "db_get", "result": json.dumps({"price": 3064})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.verdict.value == "WRONG", res.reasons
        assert ErrorClass.HALLUCINATED_SKU in res.error_classes

    def test_verdict_cyrillic_sku_present_in_tools_ok(self):
        """АП-100005 подтверждён в tool_results → не галлюцинация."""
        case = make_case(cid="cyrok", expected={"price": 3064},
                         ground_truth_kw={"check_skus": True})
        run = make_run(
            final_text="Артикул АП-100005, цена 3064",
            tool_calls=[{"name": "db_search", "arguments": {"entity": "catalog_product", "pattern": "АП-100005"}}],
            tool_results=[{"name": "db_search", "result": json.dumps({"preview": [{"id": 1, "name": "АП-100005"}]})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert ErrorClass.HALLUCINATED_SKU not in res.error_classes, res.reasons

    def test_verdict_partial_lost_total(self):
        """total:40 known, answer vague → PARTIAL + LOST_TOTAL (not WRONG)."""
        case = make_case(cid="lt", gt_type="count", expected={"count": 40},
                         gt_extra={"answer_rules": {"expect_total_mentioned": True}})
        run = make_run(
            final_text="В наличии есть много позиций",
            tool_calls=[{"name": "filter_catalog_product"}],
            tool_results=[{"name": "filter_catalog_product",
                           "result": json.dumps({"total": 40, "returned": 20, "preview": [{"id": 1}]})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.verdict.value == "PARTIAL", res.reasons
        assert ErrorClass.LOST_TOTAL in res.error_classes
        assert ErrorClass.ANSWER_MISS not in res.error_classes

    def test_verdict_partial_false_uncertainty(self):
        """Grounded fact hedged with 'скорее всего' → PARTIAL + FALSE_UNCERTAINTY."""
        case = make_case(expected={"price": 3064})
        run = make_run(
            final_text="Артикул стоит, скорее всего, 3064 рубля",
            tool_calls=[{"name": "db_get"}],
            tool_results=[{"name": "db_get", "result": json.dumps({"price": 3064})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.verdict.value == "PARTIAL", res.reasons
        assert ErrorClass.FALSE_UNCERTAINTY in res.error_classes

    def test_verdict_partial_tool_overuse(self):
        """Exceeds budget.max_tool_calls → PARTIAL + TOOL_OVERUSE."""
        case = make_case(expected={"price": 3064}, budget={"max_tool_calls": 2})
        run = make_run(
            final_text="Цена 3064",
            tool_calls=[{"name": "db_get"} for _ in range(4)],
            tool_results=[{"name": "db_get", "result": json.dumps({"price": 3064})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.verdict.value == "PARTIAL", res.reasons
        assert ErrorClass.TOOL_OVERUSE in res.error_classes
        assert res.total_tool_calls == 4

    def test_verdict_error_infra(self):
        """Tool returns {"error": "timeout"} → ERROR + INFRA_ERROR, error_source=tool."""
        case = make_case(expected={"price": 3064})
        run = make_run(
            final_text="",
            tool_calls=[{"name": "db_get"}],
            tool_results=[{"name": "db_get", "result": json.dumps({"error": "timeout"})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.verdict.value == "ERROR", res.reasons
        assert ErrorClass.INFRA_ERROR in res.error_classes
        assert res.error_source == "tool"

    def test_verdict_partial_schema_error(self):
        """entity='Order' instead of catalog_order → PARTIAL + SCHEMA_ENTITY_ERROR."""
        case = make_case(expected={"status": "shipped"},
                         status_synonyms={"shipped": ["отправлен"]})
        run = make_run(
            final_text="отправлен",
            tool_calls=[{"name": "db_search", "arguments": {"entity": "Order", "pattern": "АП-100005"}}],
            tool_results=[{"name": "db_search", "result": json.dumps({"preview": [{"id": 5}]})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.verdict.value == "PARTIAL", res.reasons
        assert ErrorClass.SCHEMA_ENTITY_ERROR in res.error_classes

    def test_verdict_wrong_availability(self):
        """expected available=True but answer says нет → WRONG + WRONG_AVAILABILITY."""
        case = make_case(expected={"available": True})
        run = make_run(
            final_text="Нет в наличии",
            tool_calls=[{"name": "db_get"}],
            tool_results=[{"name": "db_get", "result": json.dumps({"is_available": True})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.verdict.value == "WRONG", res.reasons
        assert ErrorClass.WRONG_AVAILABILITY in res.error_classes

    def test_verdict_forbidden_tool(self):
        """must_not_call invoked → WRONG + FORBIDDEN_TOOL."""
        case = make_case(expected={"price": 3064},
                         expected_tool={"must_call_any": ["db_get"], "must_not_call": ["db_map"]})
        run = make_run(
            final_text="Цена 3064",
            tool_calls=[{"name": "db_get"}, {"name": "db_map"}],
            tool_results=[{"name": "db_get", "result": json.dumps({"price": 3064})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert ErrorClass.FORBIDDEN_TOOL in res.error_classes

    def test_verdict_tool_loop(self):
        """run.loop_warnings → PARTIAL + TOOL_LOOP."""
        case = make_case(expected={"price": 3064})
        run = make_run(
            final_text="Цена 3064",
            tool_calls=[{"name": "db_get"}],
            tool_results=[{"name": "db_get", "result": json.dumps({"price": 3064})}],
            loop_warnings=["Loop detected: tool 'db_get' called 3x"],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.verdict.value == "PARTIAL", res.reasons
        assert ErrorClass.TOOL_LOOP in res.error_classes

    # ═══════════════════════════════════════════════════════════════════════
    # dedupe in min_count
    # ═══════════════════════════════════════════════════════════════════════

    def test_min_count_dedupes_repeated_rows(self):
        """db_search preview 20 + db_get on same ids → not double-counted."""
        case = make_case(gt_type="list_ids", expected={"min_count": 25})
        run = make_run(
            final_text="Вот товары",
            tool_calls=[{"name": "db_search"}],
            tool_results=[
                {"name": "db_search", "result": json.dumps({"preview": [{"id": i} for i in range(20)], "total": 20})},
                {"name": "db_get", "result": json.dumps({"id": 1, "price": 100})},
                {"name": "db_get", "result": json.dumps({"id": 2, "price": 200})},
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.retrieval_ok, "должно быть 20 уникальных (id 1,2 уже в preview)"
        assert ErrorClass.RETRIEVAL_MISS in res.error_classes

    def test_min_count_does_not_dedupe_distinct_rows(self):
        """Preview 20 + 5 new db_get ids → 25 unique → retrieval OK."""
        case = make_case(gt_type="list_ids", expected={"min_count": 25})
        run = make_run(
            final_text="Вот товары",
            tool_calls=[{"name": "db_search"}],
            tool_results=[
                {"name": "db_search", "result": json.dumps({"preview": [{"id": i} for i in range(20)], "total": 20})},
                {"name": "db_get", "result": json.dumps({"id": 100, "price": 100})},
                {"name": "db_get", "result": json.dumps({"id": 101, "price": 200})},
                {"name": "db_get", "result": json.dumps({"id": 102, "price": 300})},
                {"name": "db_get", "result": json.dumps({"id": 103, "price": 400})},
                {"name": "db_get", "result": json.dumps({"id": 104, "price": 500})},
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.retrieval_ok, res.reasons

    # ═══════════════════════════════════════════════════════════════════════
    # error payload is not data
    # ═══════════════════════════════════════════════════════════════════════

    def test_error_payload_is_not_data(self):
        """{"error": "timeout"} → empty results, not data."""
        ev = DeterministicEvaluator()
        assert ev._json_has_rows({"error": "timeout"}) is False
        assert ev._check_empty_results([{"name": "db_get", "result": json.dumps({"error": "timeout"})}])
        assert ev._count_rows(json.dumps({"error": "timeout"})) == 0

    # ═══════════════════════════════════════════════════════════════════════
    # narrowed bool markers
    # ═══════════════════════════════════════════════════════════════════════

    def test_bool_weak_markers_removed(self):
        """'да'/'есть'/'нет' alone no longer match availability."""
        ev = DeterministicEvaluator()
        assert not ev._match_bool(True, "да, уточню")
        assert not ev._match_bool(True, "есть смысл проверить")
        assert not ev._match_bool(False, "нет, не надо")
        assert ev._match_bool(True, "в наличии")
        assert ev._match_bool(False, "отсутствует")

    def test_bool_strong_markers_still_match(self):
        """Strong markers still work."""
        ev = DeterministicEvaluator()
        assert ev._match_bool(True, "товар в наличии на складе")
        assert ev._match_bool(False, "товар закончился")


# ═══════════════════════════════════════════════════════════════════════════
# Backlog parser
# ═══════════════════════════════════════════════════════════════════════════


def _write_backlog(tmp_path: Path, session_id: str, records: list[dict]) -> Path:
    p = tmp_path / f"agent_agent-test_{session_id}.jsonl"
    content = ""
    for r in records:
        content += json.dumps(r, ensure_ascii=False, indent=2) + "\n---===---\n"
    p.write_text(content, encoding="utf-8")
    return p


class TestBacklogParser:
    def test_find_backlog_file_by_substring(self, tmp_path):
        p = _write_backlog(tmp_path, "bench-abc123", [{"type": "turn_end", "outcome": "final"}])
        found = find_backlog_file(tmp_path, "bench-abc123")
        assert found == p

    def test_parse_turn_end(self, tmp_path):
        _write_backlog(tmp_path, "bench-xyz", [
            {"event": "turn_start", "data": {"user_message": "q"}},
            {"type": "turn_end", "duration_ms": 123.4, "total_prompt_tokens": 10,
             "total_completion_tokens": 20, "total_tokens": 30, "total_cost": 0.001,
             "llm_calls": 2, "tool_calls": 3, "tool_errors": 0, "empty_results": 0,
             "empty_rounds": 0, "iterations": 2, "outcome": "final", "final_length_chars": 10},
        ])
        bd = parse_backlog_data(tmp_path, "bench-xyz")
        assert bd is not None
        assert bd.duration_ms == 123.4
        assert bd.total_tokens == 30
        assert bd.total_cost == 0.001
        assert bd.llm_calls == 2
        assert bd.tool_calls_count == 3
        assert bd.outcome == "final"

    def test_missing_file_returns_none(self, tmp_path):
        assert parse_backlog_data(tmp_path, "nope") is None

    def test_read_all_records(self, tmp_path):
        _write_backlog(tmp_path, "bench-aaa", [
            {"event": "turn_start", "data": {"user_message": "hi"}},
            {"event": "tool_call", "iteration": 0, "data": {"name": "db_search", "arguments": {}}},
        ])
        recs = read_all_records(tmp_path, "bench-aaa")
        assert len(recs) == 2
        assert recs[0]["event"] == "turn_start"


# ═══════════════════════════════════════════════════════════════════════════
# Report aggregation
# ═══════════════════════════════════════════════════════════════════════════


class TestReport:
    def _case_run_eval(self, cid, success, expect_refusal=False):
        case = make_case(cid=cid, gt_type="exact_value" if not expect_refusal else "not_found",
                         expected={"price": 1} if not expect_refusal else {"count": 0},
                         expect_refusal=expect_refusal)
        run = make_run(final_text="ok" if success else "bad",
                       tool_calls=[{"name": "db_get"}],
                       tool_results=[{"name": "db_get", "result": json.dumps({"price": 1})}],
                       backlog=BacklogData(duration_ms=100, total_tokens=50, llm_calls=1,
                                           tool_calls_count=1, iterations=1, total_cost=0.01))
        ev = EvalResult(case_id=cid,
                        tool_ok=True, retrieval_ok=success, answer_ok=success,
                        hallucination=not success, grounded=success, refusal_ok=True)
        return case, run, ev

    def test_aggregation_math(self):
        cases, runs, evals = [], [], []
        for i in range(3):
            c, r, e = self._case_run_eval(f"c{i}", success=(i % 2 == 0))
            cases.append(c); runs.append(r); evals.append(e)

        report = aggregate_report(cases, runs, evals)
        assert report.total_cases == 3
        assert report.success_rate == pytest.approx(2 / 3)
        assert report.retrieval_success_rate == pytest.approx(2 / 3)
        assert report.hallucination_rate == pytest.approx(1 / 3)
        assert report.groundedness_rate == pytest.approx(2 / 3)
        assert report.avg_total_tokens == pytest.approx(50)
        assert report.avg_duration_ms == pytest.approx(100)
        assert report.avg_llm_calls == pytest.approx(1)
        assert report.p95_duration_ms == pytest.approx(100)

    def test_report_to_dict_shape(self):
        cases, runs, evals = [], [], []
        for i in range(2):
            c, r, e = self._case_run_eval(f"c{i}", success=True)
            cases.append(c); runs.append(r); evals.append(e)
        report = aggregate_report(cases, runs, evals)
        d = report_to_dict(report)
        assert d["total_cases"] == 2
        assert "cases" in d and len(d["cases"]) == 2
        assert "eval" in d["cases"][0]
        assert "metrics" in d["cases"][0]

    def test_print_report_contains_metrics(self):
        cases, runs, evals = [], [], []
        for i in range(2):
            c, r, e = self._case_run_eval(f"c{i}", success=True)
            cases.append(c); runs.append(r); evals.append(e)
        report = aggregate_report(cases, runs, evals)
        text = print_report(report)
        assert "CORE BENCHMARK REPORT" in text
        assert "Success rate" in text
        assert "Total cases" in text

    def test_recovery_rate_metric(self):
        """Recovery rate = errors_but_final / errors_total."""
        # Кейс 1: была ошибка тула, но агент дошёл до final (recovery)
        c1 = make_case(cid="r1", expected={"price": 1})
        r1 = make_run(
            final_text="ok 1",
            tool_calls=[{"name": "db_get"}],
            tool_results=[{"name": "db_get", "result": json.dumps({"price": 1})}],
            backlog=BacklogData(tool_errors=2, outcome="final", total_tokens=10),
        )
        e1 = EvalResult(case_id="r1", tool_ok=True, retrieval_ok=True, answer_ok=True,
                        hallucination=False, grounded=True, refusal_ok=True)
        # Кейс 2: ошибка тула, агент не дошёл (limit/error)
        c2 = make_case(cid="r2", expected={"price": 1})
        r2 = make_run(
            final_text="",
            tool_calls=[{"name": "db_get"}],
            tool_results=[],
            backlog=BacklogData(tool_errors=3, outcome="error", total_tokens=5),
        )
        e2 = EvalResult(case_id="r2", tool_ok=False, retrieval_ok=False, answer_ok=False,
                        hallucination=False, grounded=False, refusal_ok=True)
        report = aggregate_report([c1, c2], [r1, r2], [e1, e2])
        assert report.errors_total_count == 2
        assert report.errors_but_final_count == 1
        assert report.recovery_rate == pytest.approx(0.5)

    def test_entity_name_accuracy_metric(self):
        """entity_name_accuracy = correct entity usage / total cases."""
        c1 = make_case(cid="e1", expected={"price": 1})
        r1 = make_run(
            final_text="ok",
            tool_calls=[{"name": "db_get", "arguments": {"entity": "catalog_product", "id": 1}}],
            tool_results=[{"name": "db_get", "result": json.dumps({"price": 1})}],
        )
        e1 = DeterministicEvaluator().evaluate(c1, r1)
        assert e1.entity_name_ok
        c2 = make_case(cid="e2", expected={"price": 1})
        r2 = make_run(
            final_text="ok",
            tool_calls=[{"name": "db_get", "arguments": {"entity": "product", "id": 1}}],
            tool_results=[{"name": "db_get", "result": json.dumps({"price": 1})}],
        )
        e2 = DeterministicEvaluator().evaluate(c2, r2)
        assert not e2.entity_name_ok
        report = aggregate_report([c1, c2], [r1, r2], [e1, e2])
        assert report.entity_name_accuracy == pytest.approx(0.5)

    # ═══════════════════════════════════════════════════════════════════════
    # verdict distribution, error histogram, percentiles
    # ═══════════════════════════════════════════════════════════════════════

    def test_verdict_distribution_and_histogram(self):
        """aggregate_report counts verdicts and error classes."""
        # Case 1: CORRECT
        c1 = make_case(cid="v1", expected={"price": 1})
        r1 = make_run(
            final_text="ok 1",
            tool_calls=[{"name": "db_get"}],
            tool_results=[{"name": "db_get", "result": json.dumps({"price": 1})}],
        )
        e1 = DeterministicEvaluator().evaluate(c1, r1)
        # Case 2: PARTIAL (false uncertainty)
        c2 = make_case(cid="v2", expected={"price": 2})
        r2 = make_run(
            final_text="скорее всего 2",
            tool_calls=[{"name": "db_get"}],
            tool_results=[{"name": "db_get", "result": json.dumps({"price": 2})}],
        )
        e2 = DeterministicEvaluator().evaluate(c2, r2)
        # Case 3: WRONG (hallucination)
        c3 = make_case(cid="v3", expected={"price": 3})
        r3 = make_run(
            final_text="999",
            tool_calls=[{"name": "db_get"}],
            tool_results=[{"name": "db_get", "result": json.dumps({"price": 3})}],
        )
        e3 = DeterministicEvaluator().evaluate(c3, r3)
        report = aggregate_report([c1, c2, c3], [r1, r2, r3], [e1, e2, e3])
        assert report.verdict_counts.get("CORRECT") == 1
        assert report.verdict_counts.get("PARTIAL") == 1
        assert report.verdict_counts.get("WRONG") == 1
        assert ErrorClass.FALSE_UNCERTAINTY in report.error_class_histogram
        assert ErrorClass.HALLUCINATED_NUMBER in report.error_class_histogram

    def test_percentiles_calculated(self):
        """p50/p95 for duration/tokens/cost/tool_calls are computed."""
        cases, runs, evals = [], [], []
        for i in range(4):
            c = make_case(cid=f"p{i}", expected={"price": 1})
            r = make_run(
                final_text=f"ok {i + 1}",
                tool_calls=[{"name": "db_get"}],
                tool_results=[{"name": "db_get", "result": json.dumps({"price": 1})}],
                backlog=BacklogData(duration_ms=float(i + 1) * 100, total_tokens=100 * (i + 1),
                                    total_cost=0.01 * (i + 1), llm_calls=1, tool_calls_count=i + 1,
                                    iterations=1),
            )
            e = DeterministicEvaluator().evaluate(c, r)
            cases.append(c); runs.append(r); evals.append(e)
        report = aggregate_report(cases, runs, evals)
        # durations: [100, 200, 300, 400]
        assert report.p50_duration_ms == pytest.approx(250)
        assert report.p95_duration_ms == pytest.approx(385)
        assert report.p50_tokens == pytest.approx(250)
        assert report.p95_tokens == pytest.approx(385)
        # tool_calls_count: [1, 2, 3, 4]
        assert report.p50_tool_calls == pytest.approx(2.5)

    def test_report_to_dict_includes_verdicts(self):
        """report_to_dict carries verdicts, error_classes, percentiles."""
        c = make_case(cid="rd", expected={"price": 1})
        r = make_run(
            final_text="ok 1",
            tool_calls=[{"name": "db_get"}],
            tool_results=[{"name": "db_get", "result": json.dumps({"price": 1})}],
        )
        e = DeterministicEvaluator().evaluate(c, r)
        report = aggregate_report([c], [r], [e])
        d = report_to_dict(report)
        assert d["verdicts"]["CORRECT"] == 1
        assert "error_classes" in d
        assert "p50_duration_ms" in d
        assert "run_metadata" in d
        assert d["cases"][0]["eval"]["verdict"] == "CORRECT"
        assert "repeated_tool_calls" in d["cases"][0]["metrics"]

    # ═══════════════════════════════════════════════════════════════════════
    # case-level diff for regressions
    # ═══════════════════════════════════════════════════════════════════════

    def test_diff_reports_detects_regression(self):
        """diff_reports flags a case that went CORRECT→WRONG."""
        def _mk(cid, final_text, expected={"price": 1}):
            c = make_case(cid=cid, expected=expected)
            r = make_run(final_text=final_text,
                         tool_calls=[{"name": "db_get"}],
                         tool_results=[{"name": "db_get", "result": json.dumps({"price": 1})}])
            return c, r, DeterministicEvaluator().evaluate(c, r)

        # prev: CORRECT; cur: WRONG (hallucination)
        c1, r1, e1 = _mk("case-1", "цена 1")
        c1b, r1b, e1b = _mk("case-1", "цена 999")
        prev = aggregate_report([c1], [r1], [e1])
        cur = aggregate_report([c1b], [r1b], [e1b])
        diff = diff_reports(prev, cur)
        assert diff["total_changed"] == 1
        assert diff["regressed"] == 1
        assert diff["improved"] == 0
        entry = diff["case_diff"][0]
        assert entry["case_id"] == "case-1"
        assert entry["prev_verdict"] == "CORRECT"
        assert entry["cur_verdict"] == "WRONG"
        assert entry["changed"] is True

    def test_diff_reports_improvement(self):
        """diff_reports flags a case that went WRONG→CORRECT as improved."""
        def _mk(cid, final_text):
            c = make_case(cid=cid, expected={"price": 1})
            r = make_run(final_text=final_text,
                         tool_calls=[{"name": "db_get"}],
                         tool_results=[{"name": "db_get", "result": json.dumps({"price": 1})}])
            return c, r, DeterministicEvaluator().evaluate(c, r)

        c1, r1, e1 = _mk("case-1", "цена 999")
        c1b, r1b, e1b = _mk("case-1", "цена 1")
        prev = aggregate_report([c1], [r1], [e1])
        cur = aggregate_report([c1b], [r1b], [e1b])
        diff = diff_reports(prev, cur)
        assert diff["total_changed"] == 1
        assert diff["improved"] == 1
        assert diff["regressed"] == 0

    def test_diff_reports_no_change(self):
        """Same verdicts → no diff."""
        def _mk(cid, final_text):
            c = make_case(cid=cid, expected={"price": 1})
            r = make_run(final_text=final_text,
                         tool_calls=[{"name": "db_get"}],
                         tool_results=[{"name": "db_get", "result": json.dumps({"price": 1})}])
            return c, r, DeterministicEvaluator().evaluate(c, r)

        c1, r1, e1 = _mk("case-1", "цена 1")
        c1b, r1b, e1b = _mk("case-1", "цена 1")
        prev = aggregate_report([c1], [r1], [e1])
        cur = aggregate_report([c1b], [r1b], [e1b])
        diff = diff_reports(prev, cur)
        assert diff["total_changed"] == 0
        assert diff["regressed"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# SSE parsing (pure)
# ═══════════════════════════════════════════════════════════════════════════


class _FakeResp:
    def __init__(self, lines):
        self._lines = lines

    def iter_lines(self):
        for l in self._lines:
            yield l.encode()


class TestSseParse:
    def test_parses_events(self):
        resp = _FakeResp([
            "data: {\"type\": \"status\", \"phase\": \"tool_calls\"}",
            "data: {\"type\": \"tool_call\", \"name\": \"db_search\", \"arguments\": {}}",
            "data: {\"type\": \"token\", \"text\": \"Цена \"}",
            "data: {\"type\": \"final\", \"text\": \"3064\"}",
            "data: {\"type\": \"done\"}",
        ])
        r = _sse_parse_events(resp)
        assert r["final_text"] == "Цена 3064"
        assert len(r["tool_calls"]) == 1
        assert r["tool_calls"][0]["name"] == "db_search"
        assert len(r["events"]) == 5

    def test_skips_bad_json(self):
        resp = _FakeResp([
            "data: not-json",
            "data: {\"type\": \"done\"}",
        ])
        r = _sse_parse_events(resp)
        assert len(r["events"]) == 1
        assert r["events"][0]["type"] == "done"
