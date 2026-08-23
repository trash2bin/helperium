"""Deterministic unit tests for the Core Benchmark (no LLM, no network).

Covers:
- evaluator: tool/retrieval/answer/hallucination/refusal logic
- backlog_parser: session-file lookup + turn_end parsing
- report: aggregation math + JSON serialization
- runner: SSE parsing (pure function)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
import requests
from typer.testing import CliRunner

from agent_db.bench import cli as bench_cli
from agent_db.bench.evaluator import DeterministicEvaluator
from agent_db.bench.models import (
    BacklogData,
    Verdict,
    BenchmarkReport,
    EvalResult,
    ErrorClass,
    RunResult,
    TestCase,
)
from agent_db.bench.report import (
    aggregate_report,
    diff_reports,
    print_report,
    report_to_dict,
)
from agent_db.bench.reader import find_backlog_dir
from agent_db.bench.backlog_parser import (
    find_backlog_file,
    parse_backlog_data,
    read_all_records,
)
from agent_db.bench.run_guard import BenchmarkRunGuard, BenchmarkRunInProgressError
from agent_db.bench.runner import (
    BenchmarkPreflightError,
    BenchmarkRunner,
    _sse_parse_events,
)
from agent_db.bench.agent_policy import (
    AUTOPARTS_BENCHMARK_SYSTEM_PROMPT,
    sync_autoparts_benchmark_agent_policy,
)
from agent_db.bench.cli import _load_cases

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


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


class TestCaseFixture:
    def test_deprecated_discount_cases_are_excluded_from_active_scoring(self):
        """Ambiguous legacy discounts remain inspectable but never enter default scoring."""
        cases_path = (
            Path(__file__).resolve().parents[1]
            / "agent_db"
            / "bench"
            / "cases"
            / "autoparts.json"
        )
        active_cases = _load_cases(cases_path)
        all_cases = _load_cases(cases_path, include_deprecated=True)
        active_ids = {case.id for case in active_cases}
        all_by_id = {case.id: case for case in all_cases}

        assert len(active_cases) == 49
        assert len(all_cases) == 51
        assert {
            "product-count-price-discount-001",
            "product-count-promo-label-001",
        } <= active_ids
        for case_id in ("product-count-discount-001", "product-filter-discount-001"):
            assert case_id not in active_ids
            assert all_by_id[case_id].deprecated
            assert all_by_id[case_id].replaced_by == [
                "product-count-price-discount-001",
                "product-count-promo-label-001",
            ]


class TestEvaluator:
    def test_correct_lookup_passes(self):
        case = make_case(
            expected={"price": 3064},
            expected_tool={"must_call_any": ["filter_catalog_product", "db_get"]},
        )
        run = make_run(
            final_text="Цена артикула: 3064 рубля",
            tool_calls=[
                {
                    "name": "db_get",
                    "arguments": {"entity": "catalog_product", "id": 392},
                }
            ],
            tool_results=[
                {"name": "db_get", "result": json.dumps({"id": 392, "price": 3064})}
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.tool_ok and res.retrieval_ok and res.answer_ok
        assert not res.hallucination and res.grounded
        assert res.success

    @pytest.mark.parametrize(
        ("case_id", "entity", "total"),
        [
            ("category-count-001", "catalog_category", 117),
            ("product-count-total-001", "catalog_product", 407),
            ("order-count-total-001", "catalog_order", 6),
        ],
    )
    def test_count_fixture_accepts_db_describe_total(self, case_id, entity, total):
        """Entity describe total is authoritative for unfiltered count cases."""
        cases_path = (
            Path(__file__).resolve().parents[1]
            / "agent_db"
            / "bench"
            / "cases"
            / "autoparts.json"
        )
        raw_case = next(
            item
            for item in json.loads(cases_path.read_text(encoding="utf-8"))["cases"]
            if item["id"] == case_id
        )
        assert "db_describe" in raw_case["expected_tool"]["must_call_any"]
        case = TestCase.from_dict(raw_case)
        run = make_run(
            final_text=f"Всего {total}.",
            tool_calls=[{"name": "db_describe", "arguments": {"entity": entity}}],
            tool_results=[
                {"name": "db_describe", "result": json.dumps({"total": total})}
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.tool_ok, res.reasons
        assert res.verdict.value == "CORRECT", res.reasons

    @pytest.mark.parametrize(
        ("case_id", "arguments", "total"),
        [
            ("product-count-price-discount-001", {"old_price__gt": 0}, 72),
            ("product-count-promo-label-001", {"label__in": ["sale", "promo"]}, 49),
        ],
    )
    def test_explicit_discount_count_fixture_accepts_filtered_total(
        self, case_id, arguments, total
    ):
        """Each explicit discount signal is grounded by its own filtered total."""
        cases_path = (
            Path(__file__).resolve().parents[1]
            / "agent_db"
            / "bench"
            / "cases"
            / "autoparts.json"
        )
        raw_case = next(
            item
            for item in json.loads(cases_path.read_text(encoding="utf-8"))["cases"]
            if item["id"] == case_id
        )
        case = TestCase.from_dict(raw_case)
        run = make_run(
            final_text=f"Всего {total}.",
            tool_calls=[{"name": "filter_catalog_product", "arguments": arguments}],
            tool_results=[
                {
                    "name": "filter_catalog_product",
                    "result": json.dumps({"total": total}),
                }
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.verdict.value == "CORRECT", res.reasons

    def test_narrow_nbsp_price_is_grounded(self):
        """2\u202f418 in a user-facing answer equals tool price 2418."""
        case = make_case(
            expected={"price": 2418},
            expected_tool={"must_call_any": ["db_get"]},
        )
        run = make_run(
            final_text="Цена: 2\u202f418 руб.",
            tool_calls=[
                {
                    "name": "db_get",
                    "arguments": {"entity": "catalog_product", "id": 367},
                }
            ],
            tool_results=[
                {"name": "db_get", "result": json.dumps({"id": 367, "price": 2418})}
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.hallucination, res.reasons
        assert res.verdict.value == "CORRECT", res.reasons

    def test_polite_please_is_not_uncertainty(self):
        """The marker «пожалуй» must not match the polite word «пожалуйста»."""
        case = make_case(
            expected={"price": 2418},
            expected_tool={"must_call_any": ["db_get"]},
        )
        run = make_run(
            final_text="Цена: 2418 руб. Уточните, пожалуйста.",
            tool_calls=[{"name": "db_get"}],
            tool_results=[{"name": "db_get", "result": json.dumps({"price": 2418})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert ErrorClass.FALSE_UNCERTAINTY not in res.error_classes
        assert res.verdict.value == "CORRECT", res.reasons

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
        case = make_case(
            expected={"price": 3064},
            expected_tool={"must_call_any": ["filter_catalog_product", "db_get"]},
        )
        run = make_run(
            final_text="3064",
            tool_calls=[{"name": "db_map"}],
            tool_results=[{"name": "db_map", "result": "{}"}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.tool_ok
        assert not res.success

    def test_forbidden_tool_fails(self):
        case = make_case(
            expected={"price": 3064},
            expected_tool={
                "must_call_any": ["db_get"],
                "must_not_call": ["db_describe"],
            },
        )
        run = make_run(
            final_text="3064",
            tool_calls=[{"name": "db_get"}, {"name": "db_describe"}],
            tool_results=[{"name": "db_get", "result": json.dumps({"price": 3064})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.tool_ok

    def test_absence_correct_refusal_passes(self):
        case = make_case(
            cid="abs",
            gt_type="not_found",
            expected={"count": 0},
            expect_refusal=True,
            expected_tool={"must_call_any": ["filter_catalog_product"]},
        )
        run = make_run(
            final_text="Такого артикула не найдено в каталоге.",
            tool_calls=[{"name": "filter_catalog_product"}],
            tool_results=[
                {
                    "name": "filter_catalog_product",
                    "result": '{"empty_hint": {"available_values": {"article": ["A1"]}}}',
                }
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.retrieval_ok and res.answer_ok and res.refusal_ok
        assert not res.hallucination and res.success

    def test_absence_invented_data_is_hallucination(self):
        case = make_case(
            cid="abs2", gt_type="not_found", expected={"count": 0}, expect_refusal=True
        )
        run = make_run(
            final_text="Да, артикул есть, цена 1000 рублей.",
            tool_calls=[{"name": "filter_catalog_product"}],
            tool_results=[{"name": "filter_catalog_product", "result": "[]"}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.refusal_ok and res.hallucination and not res.success

    # absence-кейс, где модель зовёт db_map (схема) — это НЕ данные.
    def test_absence_with_db_map_still_empty(self):
        case = make_case(
            cid="abs3",
            gt_type="not_found",
            expected={"count": 0},
            expect_refusal=True,
            expected_tool={"must_call_any": ["db_search"]},
        )
        run = make_run(
            final_text="Артикул ZZ-000-NOPE не найден в каталоге.",
            tool_calls=[{"name": "db_map"}, {"name": "db_search"}],
            tool_results=[
                {
                    "name": "db_map",
                    "result": json.dumps({"entities": [{"name": "product"}]}),
                },
                {
                    "name": "db_search",
                    "result": json.dumps(
                        {"empty_hint": {"available_values": {"article": ["A1"]}}}
                    ),
                },
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.retrieval_ok, (
            f"db_map не данные, retrieval должен быть пуст: {res.reasons}"
        )
        assert res.refusal_ok and not res.hallucination and res.success, res.reasons

    def test_status_with_synonyms(self):
        case = make_case(
            expected={"status": "shipped"},
            status_synonyms={"shipped": ["отправлен", "в пути"]},
        )
        run = make_run(
            final_text="Статус: отправлен",
            tool_calls=[{"name": "filter_catalog_order"}],
            tool_results=[
                {
                    "name": "filter_catalog_order",
                    "result": json.dumps({"status": "shipped"}),
                }
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.answer_ok and res.success

    @pytest.mark.parametrize(
        ("final_text", "answer_ok"),
        [
            ("Заказ оплачен онлайн.", True),
            ("Выбран способ: онлайн-оплата.", True),
            ("Заказ не оплачен онлайн, выбран другой способ.", False),
            ("Заказ оплачен картой.", False),
        ],
    )
    def test_exact_value_uses_fixture_scoped_value_aliases(self, final_text, answer_ok):
        case = make_case(
            expected={"payment": "online"},
            ground_truth_kw={
                "value_aliases": {"payment": {"online": ["онлайн", "онлайн-оплата"]}}
            },
        )
        run = make_run(
            final_text=final_text,
            tool_calls=[{"name": "db_get"}],
            tool_results=[
                {"name": "db_get", "result": json.dumps({"payment": "online"})}
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.answer_ok is answer_ok, res.reasons
        assert res.answer_completeness == (1.0 if answer_ok else 0.0)

    def test_value_aliases_do_not_enable_generic_fuzzy_matching(self):
        case = make_case(expected={"payment": "online"})
        run = make_run(
            final_text="Заказ оплачен онлайн.",
            tool_calls=[{"name": "db_get"}],
            tool_results=[
                {"name": "db_get", "result": json.dumps({"payment": "online"})}
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.answer_ok

    def test_count_wrong_number(self):
        case = make_case(cid="cnt", gt_type="count", expected={"count": 74})
        run = make_run(
            final_text="Нашёл 100 товаров",
            tool_calls=[{"name": "filter_catalog_product"}],
            tool_results=[
                {"name": "filter_catalog_product", "result": json.dumps({"total": 74})}
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.answer_ok and res.hallucination

    def test_list_ids_min_count(self):
        case = make_case(gt_type="list_ids", expected={"min_count": 1})
        run = make_run(
            final_text="Вот товары: EXT-01392, EXT-01367",
            tool_calls=[{"name": "filter_catalog_product"}],
            tool_results=[
                {
                    "name": "filter_catalog_product",
                    "result": json.dumps({"preview": [{"id": 1}, {"id": 2}]}),
                }
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.retrieval_ok and res.answer_ok and res.success

    def test_structured_empty_preview_is_empty(self):
        """data-service returns {preview:[],returned:0,total:0} — treat as empty."""
        ev = DeterministicEvaluator()
        assert ev._check_empty_results(
            [{"name": "f", "result": '{"preview": [], "returned": 0, "total": 0}'}]
        )
        assert not ev._check_empty_results(
            [
                {
                    "name": "f",
                    "result": '{"preview": [{"id": 1}], "returned": 1, "total": 1}',
                }
            ]
        )

    def test_price_with_thousand_separator_matches(self):
        """Answer "3 064" must match expected 3064."""
        case = make_case(expected={"price": 3064})
        run = make_run(
            final_text="Цена: 3 064 рубля",
            tool_calls=[{"name": "db_get"}],
            tool_results=[{"name": "db_get", "result": json.dumps({"price": 3064})}],
        )
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
            tool_results=[
                {
                    "name": "db_get",
                    "result": json.dumps({"is_available": True, "quantity": 23}),
                }
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.answer_ok, f"bool True должен матчиться с «в наличии»: {res.reasons}"
        assert res.success, res.reasons

    def test_bool_not_available_ru_semantic(self):
        case = make_case(expected={"available": False, "quantity": 0})
        run = make_run(
            final_text="Артикул закончился, на складе 0 шт.",
            tool_calls=[{"name": "db_get"}],
            tool_results=[
                {
                    "name": "db_get",
                    "result": json.dumps({"is_available": False, "quantity": 0}),
                }
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.answer_ok, (
            f"bool False должен матчиться с «закончился»: {res.reasons}"
        )
        assert res.success, res.reasons

    # ── производные числа (суммы) ───────────────
    # Бенч (order-lookup-total): модель считает 677×3=2031 (line-items),
    # эти числа в ответе, но не в tool_results → false-positive hallucination.
    def test_derived_arithmetic_not_hallucination(self):
        case = make_case(expected={"total": 7809})
        run = make_run(
            final_text="Заказ АП-100004: итого 7809 руб. (677×3 позиции = 2031 за детали)",
            tool_calls=[{"name": "db_get"}],
            tool_results=[{"name": "db_get", "result": json.dumps({"total": 7809})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.answer_ok and not res.hallucination, (
            f"производная арифметика не галлюцинация: {res.reasons}"
        )
        assert res.success, res.reasons

    def test_derived_count_not_hallucination(self):
        case = make_case(cid="dc", gt_type="count", expected={"count": 42})
        run = make_run(
            final_text="Найдено 42 товара (плюс ещё 5 в других категориях)",
            tool_calls=[{"name": "filter_catalog_product"}],
            tool_results=[
                {"name": "filter_catalog_product", "result": json.dumps({"total": 42})}
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.hallucination, (
            f"производное «ещё 5» не галлюцинация: {res.reasons}"
        )

    # сумма позиций заказа (2031 = 677×3, где 677 и 3 в tool_results).
    def test_line_item_sum_not_hallucination(self):
        case = make_case(expected={"total": 7809})
        run = make_run(
            final_text="Заказ: итого 7809 ₽. Позиция FLT-01188: 2 031 ₽ (677×3).",
            tool_calls=[{"name": "db_get"}],
            tool_results=[
                {
                    "name": "db_get",
                    "result": json.dumps(
                        {
                            "total": 7809,
                            "items": [
                                {"price": 677, "quantity": 3},
                                {"price": 4585, "quantity": 1},
                            ],
                        }
                    ),
                }
            ],
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
            tool_results=[
                {"name": "filter_catalog_product", "result": json.dumps({"total": 700})}
            ],
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
            tool_results=[
                {
                    "name": "filter_catalog_product",
                    "result": json.dumps(
                        {
                            "preview": [
                                {"id": 1, "quantity": 20},
                                {"id": 2, "quantity": 35},
                            ]
                        }
                    ),
                }
            ],
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
            tool_results=[
                {"name": "filter_catalog_product", "result": json.dumps({"total": 40})}
            ],
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
            tool_results=[
                {"name": "filter_catalog_product", "result": json.dumps({"total": 40})}
            ],
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
            tool_results=[
                {"name": "filter_catalog_product", "result": json.dumps({"total": 40})}
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.hallucination, (
            f"999 не подтверждён и не выводим → галлюцинация: {res.reasons}"
        )

    # ── табличные № строк ───────────────────────
    # Модель нумерует строки в таблице/списке (1..25) — это не факты.
    def test_table_row_numbers_not_hallucination(self):
        """Числа 10..14 в начале строк списка (номера) не галлюцинация."""
        case = make_case(cid="rows", gt_type="list_ids", expected={"min_count": 3})
        run = make_run(
            final_text="Вот товары:\n- 10. Амортизатор KYB\n- 11. Амортизатор Sachs\n- 12. Амортизатор Monroe\n- 13. Амортизатор Boge\n- 14. Амортизатор Bilstein",
            tool_calls=[{"name": "filter_catalog_product"}],
            tool_results=[
                {
                    "name": "filter_catalog_product",
                    "result": json.dumps(
                        {
                            "preview": [
                                {"id": 10},
                                {"id": 11},
                                {"id": 12},
                                {"id": 13},
                                {"id": 14},
                            ],
                            "total": 5,
                        }
                    ),
                }
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.hallucination, f"номера строк не галлюцинация: {res.reasons}"

    def test_table_row_numbers_in_markdown_table(self):
        """| 12 | Товар | — номер строки таблицы, не факт."""
        case = make_case(cid="rows2", gt_type="list_ids", expected={"min_count": 2})
        run = make_run(
            final_text="| № | Название |\n|---|----------|\n| 12 | Датчик ABS |\n| 13 | Колодки |",
            tool_calls=[{"name": "filter_catalog_product"}],
            tool_results=[
                {
                    "name": "filter_catalog_product",
                    "result": json.dumps(
                        {"preview": [{"id": 12}, {"id": 13}], "total": 2}
                    ),
                }
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.hallucination, (
            f"номера строк таблицы не галлюцинация: {res.reasons}"
        )

    def test_row_number_26_not_hallucination(self):
        """Нумерация списка до 26+ (32 товара) — номера, не факты."""
        case = make_case(cid="rows26", gt_type="list_ids", expected={"min_count": 3})
        run = make_run(
            final_text="Список:\n1. Товар A\n2. Товар B\n...\n25. Товар Y\n26. Товар Z\n27. Товар W",
            tool_calls=[{"name": "filter_catalog_product"}],
            tool_results=[
                {
                    "name": "filter_catalog_product",
                    "result": json.dumps(
                        {"preview": [{"id": i} for i in range(1, 28)], "total": 27}
                    ),
                }
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.hallucination, f"номера 26/27 не галлюцинация: {res.reasons}"

    def test_breakdown_numbers_with_confirmed_total_not_hallucination(self):
        """Агент подтвердил total=74 и разбил по категориям (12, 12, 6...) —
        breakdown-числа с суммой ≤ total не галлюцинация."""
        case = make_case(
            cid="bd",
            gt_type="count",
            expected={"count": 74},
            gt_extra={"breakdown_allowed": True},
        )
        run = make_run(
            final_text="Всего 74 товара: свечи 12, колодки 12, диски 6, фильтры 24, помпы 5",
            tool_calls=[{"name": "filter_catalog_product"}],
            tool_results=[
                {
                    "name": "filter_catalog_product",
                    "result": json.dumps(
                        {
                            "total": 74,
                            "returned": 74,
                            "preview": [{"id": i} for i in range(5)],
                        }
                    ),
                }
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.hallucination, f"breakdown не галлюцинация: {res.reasons}"

    def test_breakdown_with_total_in_sum_not_hallucination(self):
        """Total (74) в ответе + breakdown: сумма breakdown НЕ включает сам total
        (регрессия: 74+12+5 > 74 ломал фикс)."""
        case = make_case(
            cid="bd2",
            gt_type="count",
            expected={"count": 74},
            gt_extra={"breakdown_allowed": True},
        )
        run = make_run(
            final_text="Всего 74 товара в наличии: свечи 12, колодки 12, диски 6",
            tool_calls=[{"name": "filter_catalog_product"}],
            tool_results=[
                {
                    "name": "filter_catalog_product",
                    "result": json.dumps(
                        {
                            "total": 74,
                            "returned": 74,
                            "preview": [{"id": i} for i in range(5)],
                        }
                    ),
                }
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.hallucination, (
            f"breakdown c total в сумме не галлюцинация: {res.reasons}"
        )

    def test_lost_total_ignores_db_map_total(self):
        """total=407 из db_map (схема) не должен ломать LOST_TOTAL для count=74."""
        case = make_case(
            cid="lt2",
            gt_type="count",
            expected={"count": 74},
            gt_extra={"answer_rules": {"expect_total_mentioned": True}},
        )
        run = make_run(
            final_text="Всего 74 товара в наличии",
            tool_calls=[{"name": "db_map"}, {"name": "filter_catalog_product"}],
            tool_results=[
                {"name": "db_map", "result": json.dumps({"total": 407})},
                {"name": "filter_catalog_product", "result": json.dumps({"total": 74})},
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert ErrorClass.LOST_TOTAL not in res.error_classes, res.error_classes

    def test_lost_total_accepts_one_digit_count(self):
        """A standalone one-digit total is a valid count, not a code fragment."""
        case = make_case(
            cid="lt-one-digit",
            gt_type="count",
            expected={"count": 5},
            ground_truth_kw={"answer_rules": {"expect_total_mentioned": True}},
        )
        run = make_run(
            final_text="В каталоге 5 товаров бренда Hella.",
            tool_calls=[{"name": "filter_catalog_product"}],
            tool_results=[
                {"name": "filter_catalog_product", "result": json.dumps({"total": 5})}
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert ErrorClass.LOST_TOTAL not in res.error_classes, res.reasons
        assert res.verdict.value == "CORRECT", res.reasons

    def test_extract_numbers_keeps_standalone_one_digit_and_skips_codes(self):
        numbers = DeterministicEvaluator._extract_numbers(
            "5 товаров, модель V5, код A1", include_single_digit=True
        )
        assert numbers == ["5"]

    def test_refusal_catalog_missing_allows_explanatory_details(self):
        """Soft absence policy: a clear catalog refusal is sufficient."""
        case = make_case(
            cid="refusal-catalog-missing",
            gt_type="not_found",
            expected={"count": 0},
            expect_refusal=True,
        )
        run = make_run(
            final_text=(
                "Товара с артикулом ZZ-000-NOPE в каталоге нет. "
                "Доступны артикулы BRK-01001 и BRK-01005."
            ),
            tool_calls=[{"name": "filter_catalog_product"}],
            tool_results=[
                {
                    "name": "filter_catalog_product",
                    "result": json.dumps({"returned": 0, "total": 0}),
                }
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.refusal_ok
        assert not res.hallucination
        assert res.verdict.value == "CORRECT", res.reasons

    def test_refusal_v_base_net(self):
        """Отказ «Нет, товара в базе нет» — корректный refusal."""
        case = make_case(
            cid="rf1", gt_type="not_found", expected={"count": 0}, expect_refusal=True
        )
        run = make_run(
            final_text="Нет, товара с артикулом FAKE-999 в нашей базе нет.",
            tool_calls=[{"name": "filter_catalog_product"}],
            tool_results=[
                {
                    "name": "filter_catalog_product",
                    "result": json.dumps({"empty_hint": {}}),
                }
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.refusal_ok, res.reasons
        assert ErrorClass.REFUSAL_MISSING not in res.error_classes, res.error_classes

    def test_bool_match_respects_key_in_json(self):
        """JSON с is_available=true и is_bestseller=false: available должен
        матчиться по своему ключу, а не по чужому false."""
        case = make_case(expected={"available": True})
        run = make_run(
            final_text="Товар в наличии",
            tool_calls=[{"name": "db_get"}],
            tool_results=[
                {
                    "name": "db_get",
                    "result": json.dumps(
                        {"is_available": True, "is_bestseller": False, "is_new": False}
                    ),
                }
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.retrieval_ok, res.reasons
        assert res.verdict.value == "CORRECT", res.reasons

    # ── морфология стран ────────────────────────
    # «из Германии» (предложный падеж) ≠ «Германия» — матчим по корню.
    def test_country_morphology(self):
        case = make_case(expected={"country": "Германия"})
        run = make_run(
            final_text="Бренд Bosch — из Германии, основан в 1886.",
            tool_calls=[{"name": "db_get"}],
            tool_results=[
                {
                    "name": "db_get",
                    "result": json.dumps(
                        {"country": "Германия", "founded_year": 1886},
                        ensure_ascii=False,
                    ),
                }
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.answer_ok, (
            f"«из Германии» должен матчиться с «Германия»: {res.reasons}"
        )
        assert res.success, res.reasons

    def test_country_morphology_japan(self):
        case = make_case(expected={"country": "Япония"})
        run = make_run(
            final_text="Производство Denso расположено в Японии.",
            tool_calls=[{"name": "db_get"}],
            tool_results=[
                {
                    "name": "db_get",
                    "result": json.dumps({"country": "Япония"}, ensure_ascii=False),
                }
            ],
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
            tool_results=[
                {"name": "filter_catalog_product", "result": json.dumps({"total": 145})}
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.hallucination, res.reasons
        assert res.answer_ok and res.success, res.reasons

    def test_invalid_entity_name_fails(self):
        """Agent using entity='Order' instead of 'catalog_order' fails entity check."""
        case = make_case(expected={"status": "shipped"})
        run = make_run(
            final_text="shipped",
            tool_calls=[
                {
                    "name": "db_search",
                    "arguments": {"entity": "Order", "pattern": "АП-100005"},
                }
            ],
            tool_results=[
                {
                    "name": "db_search",
                    "result": json.dumps({"preview": [{"id": 5, "name": "АП-100005"}]}),
                }
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.entity_name_ok, res.reasons

    def test_valid_entity_name_passes(self):
        """Correct entity='catalog_order' passes entity check."""
        case = make_case(expected={"status": "shipped"})
        run = make_run(
            final_text="shipped",
            tool_calls=[
                {
                    "name": "db_search",
                    "arguments": {"entity": "catalog_order", "pattern": "АП-100005"},
                }
            ],
            tool_results=[
                {
                    "name": "db_search",
                    "result": json.dumps({"preview": [{"id": 5, "name": "АП-100005"}]}),
                }
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.entity_name_ok, res.reasons

    def test_percent_discount_not_hallucination(self):
        """Model-computed discount % (e.g. 'скидка ~20%') is not a hallucination."""
        case = make_case(expected={"price": 3064})
        run = make_run(
            final_text="Цена 3064 руб (старая 3830, скидка ~20%)",
            tool_calls=[{"name": "db_get"}],
            tool_results=[
                {
                    "name": "db_get",
                    "result": json.dumps({"price": 3064, "old_price": 3830}),
                }
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert not res.hallucination, res.reasons
        assert res.success, res.reasons

    # ═══════════════════════════════════════════════════════════════════════
    # verdict + error taxonomy
    # ═══════════════════════════════════════════════════════════════════════

    def test_verdict_correct(self):
        """Clean case → CORRECT, no error classes."""
        case = make_case(
            expected={"price": 3064}, expected_tool={"must_call_any": ["db_get"]}
        )
        run = make_run(
            final_text="Цена 3064 рубля",
            tool_calls=[
                {"name": "db_get", "arguments": {"entity": "catalog_product", "id": 1}}
            ],
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
        case = make_case(expected={"price": 3064}, ground_truth_kw={"check_skus": True})
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
        case = make_case(
            cid="cyr", expected={"price": 3064}, ground_truth_kw={"check_skus": True}
        )
        run = make_run(
            final_text="Цена 3064, артикул АП-100005",
            tool_calls=[
                {"name": "db_get", "arguments": {"entity": "catalog_product", "id": 1}}
            ],
            tool_results=[{"name": "db_get", "result": json.dumps({"price": 3064})}],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.verdict.value == "WRONG", res.reasons
        assert ErrorClass.HALLUCINATED_SKU in res.error_classes

    def test_verdict_cyrillic_sku_present_in_tools_ok(self):
        """АП-100005 подтверждён в tool_results → не галлюцинация."""
        case = make_case(
            cid="cyrok", expected={"price": 3064}, ground_truth_kw={"check_skus": True}
        )
        run = make_run(
            final_text="Артикул АП-100005, цена 3064",
            tool_calls=[
                {
                    "name": "db_search",
                    "arguments": {"entity": "catalog_product", "pattern": "АП-100005"},
                }
            ],
            tool_results=[
                {
                    "name": "db_search",
                    "result": json.dumps({"preview": [{"id": 1, "name": "АП-100005"}]}),
                }
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert ErrorClass.HALLUCINATED_SKU not in res.error_classes, res.reasons

    def test_verdict_partial_lost_total(self):
        """total:40 known, answer vague → PARTIAL + LOST_TOTAL (not WRONG)."""
        case = make_case(
            cid="lt",
            gt_type="count",
            expected={"count": 40},
            gt_extra={"answer_rules": {"expect_total_mentioned": True}},
        )
        run = make_run(
            final_text="В наличии есть много позиций",
            tool_calls=[{"name": "filter_catalog_product"}],
            tool_results=[
                {
                    "name": "filter_catalog_product",
                    "result": json.dumps(
                        {"total": 40, "returned": 20, "preview": [{"id": 1}]}
                    ),
                }
            ],
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
            tool_results=[
                {"name": "db_get", "result": json.dumps({"error": "timeout"})}
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.verdict.value == "ERROR", res.reasons
        assert ErrorClass.INFRA_ERROR in res.error_classes
        assert res.error_source == "tool"

    def test_verdict_error_runner_request_failure(self):
        """Request-level timeout is infra, even before a tool result exists."""
        case = make_case(expected={"price": 3064})
        run = make_run(errors=["Request failed: timed out"])
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.verdict.value == "ERROR", res.reasons
        assert ErrorClass.INFRA_ERROR in res.error_classes
        assert res.error_source == "infra"

    def test_client_validation_tool_error_is_not_infra(self):
        """HTTP 400/422 from invalid tool arguments is an agent-side error."""
        run = make_run(
            tool_results=[
                {
                    "name": "filter_catalog_product",
                    "result": json.dumps(
                        {
                            "ok": False,
                            "error": "endpoint returned status 400: parse_error",
                        }
                    ),
                }
            ]
        )
        assert DeterministicEvaluator._detect_tool_errors(run) == []

    def test_structured_agent_dispatch_errors_are_not_infra(self):
        run = make_run(
            tool_results=[
                {
                    "name": "invented_tool",
                    "result": json.dumps(
                        {
                            "ok": False,
                            "error": "Запрошенный инструмент недоступен для этого агента.",
                            "error_code": "TOOL_NOT_FOUND",
                        }
                    ),
                },
                {
                    "name": "filter_catalog_product",
                    "result": json.dumps(
                        {
                            "ok": False,
                            "error": "invalid tool arguments",
                            "error_code": "ARGUMENT_VALIDATION_FAILED",
                        }
                    ),
                },
            ]
        )
        assert DeterministicEvaluator._detect_tool_errors(run) == []

    def test_server_tool_error_is_infra(self):
        run = make_run(
            tool_results=[
                {
                    "name": "filter_catalog_product",
                    "result": json.dumps(
                        {
                            "ok": False,
                            "error": "endpoint returned status 500: database unavailable",
                        }
                    ),
                }
            ]
        )
        assert len(DeterministicEvaluator._detect_tool_errors(run)) == 1

    def test_verdict_partial_schema_error(self):
        """entity='Order' instead of catalog_order → PARTIAL + SCHEMA_ENTITY_ERROR."""
        case = make_case(
            expected={"status": "shipped"}, status_synonyms={"shipped": ["отправлен"]}
        )
        run = make_run(
            final_text="отправлен",
            tool_calls=[
                {
                    "name": "db_search",
                    "arguments": {"entity": "Order", "pattern": "АП-100005"},
                }
            ],
            tool_results=[
                {"name": "db_search", "result": json.dumps({"preview": [{"id": 5}]})}
            ],
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
            tool_results=[
                {"name": "db_get", "result": json.dumps({"is_available": True})}
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.verdict.value == "WRONG", res.reasons
        assert ErrorClass.WRONG_AVAILABILITY in res.error_classes

    # ═══════════════════════════════════════════════════════════════════════
    # golden regression: Camry incident (doc/benchmark/incident-camry.md)
    # ═══════════════════════════════════════════════════════════════════════

    def test_camry_incident_golden(self):
        """Golden Camry fixture: agent knew total=40 but said "много",
        hedged "скорее всего" with exact data, and overused db_get.
        Expect PARTIAL + TOOL_OVERUSE + LOST_TOTAL + FALSE_UNCERTAINTY,
        and NOT a hallucination / answer miss."""
        data = json.loads(
            (FIXTURES_DIR / "camry_incident.json").read_text(encoding="utf-8")
        )
        case = TestCase.from_dict(data["case"])
        tool_calls = [
            {"name": e["name"], "arguments": e.get("arguments", {})}
            for e in data["events"]
            if e["type"] == "tool_call"
        ]
        tool_results = [
            {"name": e["name"], "result": json.dumps(e["result"], ensure_ascii=False)}
            for e in data["events"]
            if e["type"] == "tool_result"
        ]
        run = RunResult(
            session_id="camry-golden",
            question=case.question,
            final_text=data["final_text"],
            tool_calls=tool_calls,
            tool_results=tool_results,
        )

        res = DeterministicEvaluator().evaluate(case, run)
        assert res.verdict.value == "PARTIAL", res.reasons
        assert ErrorClass.TOOL_OVERUSE in res.error_classes, res.error_classes
        assert ErrorClass.LOST_TOTAL in res.error_classes, res.error_classes
        assert ErrorClass.FALSE_UNCERTAINTY in res.error_classes, res.error_classes
        assert ErrorClass.HALLUCINATED_SKU not in res.error_classes, res.error_classes
        assert ErrorClass.ANSWER_MISS not in res.error_classes, res.error_classes
        assert res.total_tool_calls >= 5  # the real incident did 11 tool calls

    def test_verdict_forbidden_tool(self):
        """must_not_call invoked → WRONG + FORBIDDEN_TOOL."""
        case = make_case(
            expected={"price": 3064},
            expected_tool={"must_call_any": ["db_get"], "must_not_call": ["db_map"]},
        )
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
                {
                    "name": "db_search",
                    "result": json.dumps(
                        {"preview": [{"id": i} for i in range(20)], "total": 20}
                    ),
                },
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
                {
                    "name": "db_search",
                    "result": json.dumps(
                        {"preview": [{"id": i} for i in range(20)], "total": 20}
                    ),
                },
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
        assert ev._check_empty_results(
            [{"name": "db_get", "result": json.dumps({"error": "timeout"})}]
        )
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
    def test_find_api_service_runtime_backlog_dir(self, tmp_path, monkeypatch):
        project = tmp_path / "project"
        (project / ".git").mkdir(parents=True)
        runtime_backlog = project / "services" / "api-service" / "backlog"
        runtime_backlog.mkdir(parents=True)
        (runtime_backlog / "agent_test.jsonl").write_text("{}", encoding="utf-8")
        legacy_backlog = project / "backlog"
        legacy_backlog.mkdir()
        (legacy_backlog / "agent_legacy.jsonl").write_text("{}", encoding="utf-8")
        workdir = project / "services" / "agent-db"
        workdir.mkdir(parents=True)
        monkeypatch.delenv("BACKLOG_DIR", raising=False)
        monkeypatch.chdir(workdir)
        assert find_backlog_dir() == runtime_backlog.resolve()

    def test_find_backlog_file_by_substring(self, tmp_path):
        p = _write_backlog(
            tmp_path, "bench-abc123", [{"type": "turn_end", "outcome": "final"}]
        )
        found = find_backlog_file(tmp_path, "bench-abc123")
        assert found == p

    def test_parse_turn_end(self, tmp_path):
        _write_backlog(
            tmp_path,
            "bench-xyz",
            [
                {"event": "turn_start", "data": {"user_message": "q"}},
                {
                    "type": "turn_end",
                    "duration_ms": 123.4,
                    "total_prompt_tokens": 10,
                    "total_completion_tokens": 20,
                    "total_tokens": 30,
                    "total_cost": 0.001,
                    "llm_calls": 2,
                    "tool_calls": 3,
                    "tool_errors": 0,
                    "empty_results": 0,
                    "empty_rounds": 0,
                    "iterations": 2,
                    "outcome": "final",
                    "final_length_chars": 10,
                },
            ],
        )
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
        _write_backlog(
            tmp_path,
            "bench-aaa",
            [
                {"event": "turn_start", "data": {"user_message": "hi"}},
                {
                    "event": "tool_call",
                    "iteration": 0,
                    "data": {"name": "db_search", "arguments": {}},
                },
            ],
        )
        recs = read_all_records(tmp_path, "bench-aaa")
        assert len(recs) == 2
        assert recs[0]["event"] == "turn_start"


# ═══════════════════════════════════════════════════════════════════════════
# Report aggregation
# ═══════════════════════════════════════════════════════════════════════════


class TestReport:
    def _case_run_eval(self, cid, success, expect_refusal=False):
        case = make_case(
            cid=cid,
            gt_type="exact_value" if not expect_refusal else "not_found",
            expected={"price": 1} if not expect_refusal else {"count": 0},
            expect_refusal=expect_refusal,
        )
        run = make_run(
            final_text="ok" if success else "bad",
            tool_calls=[{"name": "db_get"}],
            tool_results=[{"name": "db_get", "result": json.dumps({"price": 1})}],
            backlog=BacklogData(
                duration_ms=100,
                total_tokens=50,
                llm_calls=1,
                tool_calls_count=1,
                iterations=1,
                total_cost=0.01,
            ),
        )
        ev = EvalResult(
            case_id=cid,
            tool_ok=True,
            retrieval_ok=success,
            answer_ok=success,
            hallucination=not success,
            grounded=success,
            refusal_ok=True,
            verdict=Verdict.CORRECT if success else Verdict.WRONG,
        )
        return case, run, ev

    def test_aggregation_math(self):
        cases, runs, evals = [], [], []
        for i in range(3):
            c, r, e = self._case_run_eval(f"c{i}", success=(i % 2 == 0))
            cases.append(c)
            runs.append(r)
            evals.append(e)

        report = aggregate_report(cases, runs, evals)
        assert report.total_cases == 3
        assert report.verdict_pass_rate == pytest.approx(2 / 3)
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
            cases.append(c)
            runs.append(r)
            evals.append(e)
        report = aggregate_report(cases, runs, evals)
        d = report_to_dict(report)
        assert d["total_cases"] == 2
        assert "success_rate" not in d
        assert "tool_error_rate" not in d
        assert d["verdict_pass_rate"] == pytest.approx(1.0)
        assert "infra_error_rate" in d
        assert "tool_attempt_failure_rate" in d
        assert "cases" in d and len(d["cases"]) == 2
        assert "eval" in d["cases"][0]
        assert "metrics" in d["cases"][0]

    def test_report_marks_runner_request_error_as_infra(self):
        case = make_case(cid="request-timeout", expected={"price": 1})
        run = make_run(errors=["Request failed: timed out"])
        ev = DeterministicEvaluator().evaluate(case, run)
        report = aggregate_report([case], [run], [ev])
        assert report.infra_error_count == 1
        assert report.infra_error_rate == pytest.approx(1.0)
        assert report.case_results[0].outcome == "error"

    def test_report_counts_legacy_infra_error_source_without_class(self):
        case, run, ev = self._case_run_eval("legacy-infra", success=False)
        ev.error_source = "infra"
        ev.error_classes = []
        ev.verdict = Verdict.ERROR

        report = aggregate_report([case], [run], [ev])

        assert report.infra_error_count == 1
        assert report.infra_error_rate == pytest.approx(1.0)
        assert report.error_class_histogram["INFRA_ERROR"] == 1

    def test_print_report_contains_metrics(self):
        cases, runs, evals = [], [], []
        for i in range(2):
            c, r, e = self._case_run_eval(f"c{i}", success=True)
            cases.append(c)
            runs.append(r)
            evals.append(e)
        report = aggregate_report(cases, runs, evals)
        text = print_report(report)
        assert "CORE BENCHMARK REPORT" in text
        assert "Verdict pass rate" in text
        assert "Total cases" in text

    def test_print_report_places_model_answer_under_question(self):
        case = make_case(cid="answer-visible", expected={"price": 1})
        run = make_run(final_text="Цена 1 рубль.", question="Сколько стоит товар?")
        ev = EvalResult(
            case_id=case.id,
            tool_ok=False,
            retrieval_ok=False,
            answer_ok=False,
            verdict=Verdict.ERROR,
        )
        text = print_report(aggregate_report([case], [run], [ev]))
        assert "Вопрос: q" in text
        assert "Ответ:  Цена 1 рубль." in text

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
        e1 = EvalResult(
            case_id="r1",
            tool_ok=True,
            retrieval_ok=True,
            answer_ok=True,
            hallucination=False,
            grounded=True,
            refusal_ok=True,
        )
        # Кейс 2: ошибка тула, агент не дошёл (limit/error)
        c2 = make_case(cid="r2", expected={"price": 1})
        r2 = make_run(
            final_text="",
            tool_calls=[{"name": "db_get"}],
            tool_results=[],
            backlog=BacklogData(tool_errors=3, outcome="error", total_tokens=5),
        )
        e2 = EvalResult(
            case_id="r2",
            tool_ok=False,
            retrieval_ok=False,
            answer_ok=False,
            hallucination=False,
            grounded=False,
            refusal_ok=True,
        )
        report = aggregate_report([c1, c2], [r1, r2], [e1, e2])
        assert report.errors_total_count == 2
        assert report.errors_but_final_count == 1
        assert report.recovery_rate == pytest.approx(0.5)

    def test_entity_name_accuracy_metric(self):
        """entity_name_accuracy = correct entity usage / total cases."""
        c1 = make_case(cid="e1", expected={"price": 1})
        r1 = make_run(
            final_text="ok",
            tool_calls=[
                {"name": "db_get", "arguments": {"entity": "catalog_product", "id": 1}}
            ],
            tool_results=[{"name": "db_get", "result": json.dumps({"price": 1})}],
        )
        e1 = DeterministicEvaluator().evaluate(c1, r1)
        assert e1.entity_name_ok
        c2 = make_case(cid="e2", expected={"price": 1})
        r2 = make_run(
            final_text="ok",
            tool_calls=[
                {"name": "db_get", "arguments": {"entity": "product", "id": 1}}
            ],
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
                backlog=BacklogData(
                    duration_ms=float(i + 1) * 100,
                    total_tokens=100 * (i + 1),
                    total_cost=0.01 * (i + 1),
                    llm_calls=1,
                    tool_calls_count=i + 1,
                    iterations=1,
                ),
            )
            e = DeterministicEvaluator().evaluate(c, r)
            cases.append(c)
            runs.append(r)
            evals.append(e)
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
            r = make_run(
                final_text=final_text,
                tool_calls=[{"name": "db_get"}],
                tool_results=[{"name": "db_get", "result": json.dumps({"price": 1})}],
            )
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
            r = make_run(
                final_text=final_text,
                tool_calls=[{"name": "db_get"}],
                tool_results=[{"name": "db_get", "result": json.dumps({"price": 1})}],
            )
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
            r = make_run(
                final_text=final_text,
                tool_calls=[{"name": "db_get"}],
                tool_results=[{"name": "db_get", "result": json.dumps({"price": 1})}],
            )
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
        resp = _FakeResp(
            [
                'data: {"type": "status", "phase": "tool_calls"}',
                'data: {"type": "tool_call", "name": "db_search", "arguments": {}}',
                'data: {"type": "token", "text": "Цена "}',
                'data: {"type": "final", "text": "Цена 3064"}',
                'data: {"type": "done"}',
            ]
        )
        r = _sse_parse_events(resp)
        assert r["final_text"] == "Цена 3064"
        assert len(r["tool_calls"]) == 1
        assert r["tool_calls"][0]["name"] == "db_search"
        assert len(r["events"]) == 5

    def test_final_event_replaces_streamed_tokens(self):
        resp = _FakeResp(
            [
                'data: {"type": "token", "text": "Полный "}',
                'data: {"type": "token", "text": "ответ"}',
                'data: {"type": "final", "text": "Полный ответ"}',
                'data: {"type": "done"}',
            ]
        )
        assert _sse_parse_events(resp)["final_text"] == "Полный ответ"

    def test_tokens_are_used_when_final_event_is_absent(self):
        resp = _FakeResp(
            [
                'data: {"type": "token", "text": "Неполный "}',
                'data: {"type": "token", "text": "поток"}',
                'data: {"type": "done"}',
            ]
        )
        assert _sse_parse_events(resp)["final_text"] == "Неполный поток"

    def test_skips_bad_json(self):
        resp = _FakeResp(
            [
                "data: not-json",
                'data: {"type": "done"}',
            ]
        )
        r = _sse_parse_events(resp)
        assert len(r["events"]) == 1
        assert r["events"][0]["type"] == "done"


class TestBenchmarkRunner:
    def test_preflight_checks_api_and_agent_before_cases(self, monkeypatch, tmp_path):
        class Response:
            def __init__(self, status_code, payload):
                self.status_code = status_code
                self._payload = payload
                self.text = json.dumps(payload)

            def json(self):
                return self._payload

        class Client:
            def __init__(self, *args, **kwargs):
                self.urls = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, url, **kwargs):
                self.urls.append(url)
                if url.endswith("/health"):
                    return Response(200, {"api": "ok"})
                return Response(200, {"name": "agent"})

        monkeypatch.setattr(httpx, "Client", Client)
        runner = BenchmarkRunner(
            api_url="http://unused",
            agent_name="agent",
            tenant_id="tenant",
            backlog_dir=tmp_path,
            bench_log_dir=tmp_path,
        )

        result = runner.preflight()

        assert result["agent_name"] == "agent"

    def test_preflight_fails_before_case_when_api_is_unavailable(
        self, monkeypatch, tmp_path
    ):
        class OfflineClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, *args, **kwargs):
                raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(httpx, "Client", OfflineClient)
        runner = BenchmarkRunner(
            api_url="http://127.0.0.1:8081",
            agent_name="agent",
            tenant_id="tenant",
            backlog_dir=tmp_path,
            bench_log_dir=tmp_path,
        )

        with pytest.raises(BenchmarkPreflightError, match="API недоступен"):
            runner.preflight()

    def test_request_failure_writes_isolated_bench_log(self, tmp_path, monkeypatch):
        class RaisingClient:
            def __init__(self, *args, **kwargs):
                raise httpx.RequestError("offline")

        monkeypatch.setattr(httpx, "Client", RaisingClient)
        runner = BenchmarkRunner(
            api_url="http://unused",
            agent_name="agent",
            tenant_id="tenant",
            backlog_dir=tmp_path,
            bench_log_dir=tmp_path,
        )

        run = runner.run_case("question", session_id="session-error")

        assert run.errors == ["Request failed: offline"]
        records = (
            (tmp_path / "session-error.bench.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        assert len(records) == 1
        record = json.loads(records[0])
        assert record["session_id"] == "session-error"
        assert record["question"] == "question"
        assert record["errors"] == ["Request failed: offline"]

    def test_requests_fallback_failure_writes_isolated_bench_log(
        self, tmp_path, monkeypatch
    ):
        def fail_request(*args, **kwargs):
            raise requests.RequestException("offline")

        monkeypatch.setitem(sys.modules, "httpx", None)
        monkeypatch.setattr(requests, "post", fail_request)
        runner = BenchmarkRunner(
            api_url="http://unused",
            agent_name="agent",
            tenant_id="tenant",
            backlog_dir=tmp_path,
            bench_log_dir=tmp_path,
        )

        run = runner.run_case("question", session_id="session-requests-error")

        assert run.errors == ["Request failed: offline"]
        records = (
            (tmp_path / "session-requests-error.bench.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        assert len(records) == 1
        assert json.loads(records[0])["errors"] == ["Request failed: offline"]


class TestBenchmarkRunGuard:
    def test_acquire_creates_uuid_scoped_evidence_and_running_manifest(self, tmp_path):
        guard = BenchmarkRunGuard(
            api_url="http://127.0.0.1:28181/",
            lock_root=tmp_path / "backlog",
            artifact_root=tmp_path / "bench-artifacts",
        )

        context = guard.acquire()

        assert len(context.run_uuid) == 32
        assert (
            context.run_dir == tmp_path / "bench-artifacts" / "runs" / context.run_uuid
        )
        assert context.lock_path.exists()
        manifest = json.loads(context.manifest_path.read_text(encoding="utf-8"))
        assert manifest["status"] == "running"
        assert manifest["api_url"] == "http://127.0.0.1:28181"
        assert manifest["run_uuid"] == context.run_uuid
        assert manifest["bench_log_dir"] == str(context.run_dir)
        assert manifest["started_at"] == context.started_at

        guard.finalize(
            status="completed", report_path=context.run_dir / "benchmark_report.json"
        )

        assert not context.lock_path.exists()
        manifest = json.loads(context.manifest_path.read_text(encoding="utf-8"))
        assert manifest["status"] == "completed"
        assert manifest["started_at"] == context.started_at
        assert manifest["completed_at"]
        assert manifest["report_path"] == str(context.run_dir / "benchmark_report.json")

    def test_second_run_cannot_bypass_lock_with_different_artifact_root(self, tmp_path):
        first = BenchmarkRunGuard(
            api_url="http://127.0.0.1:28181",
            lock_root=tmp_path / "backlog",
            artifact_root=tmp_path / "artifacts-a",
        )
        context = first.acquire()
        second = BenchmarkRunGuard(
            api_url="http://127.0.0.1:28181",
            lock_root=tmp_path / "backlog",
            artifact_root=tmp_path / "artifacts-b",
        )

        with pytest.raises(BenchmarkRunInProgressError, match="run_uuid=.*pid="):
            second.acquire()

        assert not (tmp_path / "artifacts-b" / "runs").exists()
        first.finalize(status="failed")

        replacement = second.acquire()
        assert replacement.run_uuid != context.run_uuid
        assert replacement.run_dir.parent == tmp_path / "artifacts-b" / "runs"
        second.finalize(status="completed")

    def test_cli_creates_uuid_evidence_and_releases_lock(self, tmp_path, monkeypatch):
        cases_path = tmp_path / "cases.json"
        cases_path.write_text(
            json.dumps(
                {
                    "cases": [
                        {
                            "id": "one",
                            "question": "q",
                            "category": "lookup",
                            "ground_truth": {
                                "type": "exact_value",
                                "expected": {"price": 1},
                            },
                            "expected_tool": {"must_call_any": ["db_get"]},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        runner_kwargs = {}

        class FakeRunner:
            def __init__(self, **kwargs):
                runner_kwargs.update(kwargs)
                self.bench_log_dir = Path(kwargs["bench_log_dir"])

            def preflight(self):
                return {"agent_name": "autoparts-assistant"}

            def run_case(self, question):
                return RunResult(
                    session_id="session",
                    question=question,
                    final_text="Цена 1",
                    tool_calls=[{"name": "db_get"}],
                    tool_results=[{"name": "db_get", "result": '{"price": 1}'}],
                )

        monkeypatch.setattr(bench_cli, "BenchmarkRunner", FakeRunner)
        artifact_root = tmp_path / "artifacts"
        backlog_root = tmp_path / "backlog"
        additional_report = tmp_path / "additional.json"

        result = CliRunner().invoke(
            bench_cli.app,
            [
                "run",
                str(cases_path),
                "--api-url",
                "http://127.0.0.1:28181",
                "--backlog-dir",
                str(backlog_root),
                "--bench-log-dir",
                str(artifact_root),
                "--output",
                str(additional_report),
                "--quiet",
            ],
        )

        assert result.exit_code == 0, result.output
        run_dirs = list((artifact_root / "runs").iterdir())
        assert len(run_dirs) == 1
        manifest = json.loads((run_dirs[0] / "run-manifest.json").read_text())
        report = json.loads((run_dirs[0] / "benchmark_report.json").read_text())
        assert manifest["status"] == "completed"
        assert report["run_metadata"]["run_uuid"] == manifest["run_uuid"]
        assert report["run_metadata"]["artifact_dir"] == str(run_dirs[0])
        assert runner_kwargs["timeout"] == 300.0
        assert additional_report.exists()
        assert not list((backlog_root / ".benchmark-locks").glob("*.lock"))

    def test_cli_quality_failure_releases_lock_after_writing_evidence(
        self, tmp_path, monkeypatch
    ):
        cases_path = tmp_path / "cases.json"
        cases_path.write_text(
            json.dumps(
                {
                    "cases": [
                        {
                            "id": "one",
                            "question": "q",
                            "category": "lookup",
                            "ground_truth": {
                                "type": "exact_value",
                                "expected": {"price": 1},
                            },
                            "expected_tool": {"must_call_any": ["db_get"]},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        class FailingRunner:
            def __init__(self, **kwargs):
                self.bench_log_dir = Path(kwargs["bench_log_dir"])

            def preflight(self):
                return {"agent_name": "autoparts-assistant"}

            def run_case(self, question):
                return RunResult(session_id="session", question=question)

        monkeypatch.setattr(bench_cli, "BenchmarkRunner", FailingRunner)
        artifact_root = tmp_path / "artifacts"
        backlog_root = tmp_path / "backlog"
        result = CliRunner().invoke(
            bench_cli.app,
            [
                "run",
                str(cases_path),
                "--api-url",
                "http://127.0.0.1:28181",
                "--backlog-dir",
                str(backlog_root),
                "--bench-log-dir",
                str(artifact_root),
                "--quiet",
            ],
        )

        assert result.exit_code == 1, result.output
        run_dirs = list((artifact_root / "runs").iterdir())
        assert len(run_dirs) == 1
        manifest = json.loads((run_dirs[0] / "run-manifest.json").read_text())
        assert manifest["status"] == "completed"
        assert (run_dirs[0] / "benchmark_report.json").exists()
        assert not list((backlog_root / ".benchmark-locks").glob("*.lock"))

    def test_cli_refuses_active_api_lock_before_creating_artifacts(self, tmp_path):
        cases_path = tmp_path / "cases.json"
        cases_path.write_text(
            json.dumps(
                {
                    "cases": [
                        {
                            "id": "one",
                            "question": "q",
                            "category": "lookup",
                            "ground_truth": {
                                "type": "exact_value",
                                "expected": {"price": 1},
                            },
                            "expected_tool": {"must_call_any": ["db_get"]},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        backlog_root = tmp_path / "backlog"
        artifact_root = tmp_path / "artifacts"
        holder = BenchmarkRunGuard(
            api_url="http://127.0.0.1:28181",
            lock_root=backlog_root,
            artifact_root=tmp_path / "holder-artifacts",
        )
        holder.acquire()

        result = CliRunner().invoke(
            bench_cli.app,
            [
                "run",
                str(cases_path),
                "--api-url",
                "http://127.0.0.1:28181",
                "--backlog-dir",
                str(backlog_root),
                "--bench-log-dir",
                str(artifact_root),
            ],
        )

        assert result.exit_code == 2, result.output
        assert "Benchmark already running for API" in result.output
        assert not (artifact_root / "runs").exists()
        holder.finalize(status="completed")

    def test_different_api_urls_receive_independent_locks(self, tmp_path):
        first = BenchmarkRunGuard(
            api_url="http://127.0.0.1:28181",
            lock_root=tmp_path / "backlog",
            artifact_root=tmp_path / "artifacts",
        )
        second = BenchmarkRunGuard(
            api_url="http://127.0.0.1:28182",
            lock_root=tmp_path / "backlog",
            artifact_root=tmp_path / "artifacts",
        )

        first_context = first.acquire()
        second_context = second.acquire()

        assert first_context.lock_path != second_context.lock_path
        assert first_context.run_dir != second_context.run_dir
        first.finalize(status="completed")
        second.finalize(status="completed")


class TestAggregateContract:
    def test_rejects_mismatched_case_run_and_eval_lengths(self):
        cases = [make_case("case-1"), make_case("case-2")]
        runs = [make_run()]
        evals = [EvalResult(case_id="case-1")]

        with pytest.raises(ValueError, match="must have the same length"):
            aggregate_report(cases, runs, evals)


class TestAutopartsBenchmarkAgentPolicy:
    def test_policy_requires_catalog_grounding_and_stop_after_sufficient_result(self):
        assert (
            "сначала получи подтверждение через\nMCP-инструменты"
            in AUTOPARTS_BENCHMARK_SYSTEM_PROMPT
        )
        assert (
            "поле total для вопроса о количестве" in AUTOPARTS_BENCHMARK_SYSTEM_PROMPT
        )
        assert (
            "Сохраняй пользовательские идентификаторы буквально"
            in AUTOPARTS_BENCHMARK_SYSTEM_PROMPT
        )
        assert "Не выводи внутренние рассуждения" in AUTOPARTS_BENCHMARK_SYSTEM_PROMPT
        assert "`__gt_field` / `__lt_field`" in AUTOPARTS_BENCHMARK_SYSTEM_PROMPT
        assert (
            "общий вопрос, не требующий данных каталога"
            in AUTOPARTS_BENCHMARK_SYSTEM_PROMPT
        )

    def test_sync_updates_only_system_prompt(self, monkeypatch):
        calls = []

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "name": "bench-agent",
                    "system_prompt": AUTOPARTS_BENCHMARK_SYSTEM_PROMPT,
                }

        def fake_put(url, **kwargs):
            calls.append((url, kwargs))
            return Response()

        monkeypatch.setattr("agent_db.bench.agent_policy.requests.put", fake_put)
        payload = sync_autoparts_benchmark_agent_policy(
            "http://api.test/", "bench-agent", "token"
        )

        assert payload["name"] == "bench-agent"
        assert calls == [
            (
                "http://api.test/api/agents/bench-agent",
                {
                    "headers": {"Authorization": "Bearer token"},
                    "json": {"system_prompt": AUTOPARTS_BENCHMARK_SYSTEM_PROMPT},
                    "timeout": 10.0,
                },
            )
        ]

    def test_sync_rejects_missing_admin_token(self):
        with pytest.raises(ValueError, match="admin_token"):
            sync_autoparts_benchmark_agent_policy("http://api.test", "bench-agent", "")


class TestAutopartsDeterministicFixtureRegressions:
    def _raw_case(self, case_id):
        cases_path = (
            Path(__file__).resolve().parents[1]
            / "agent_db"
            / "bench"
            / "cases"
            / "autoparts.json"
        )
        return next(
            item
            for item in json.loads(cases_path.read_text(encoding="utf-8"))["cases"]
            if item["id"] == case_id
        )

    def test_order_total_fixture_accepts_stats(self):
        raw_case = self._raw_case("order-count-total-001")
        assert "stats" in raw_case["expected_tool"]["must_call_any"]
        case = TestCase.from_dict(raw_case)
        run = make_run(
            final_text="Всего заказов: 6.",
            tool_calls=[{"name": "stats", "arguments": {}}],
            tool_results=[
                {"name": "stats", "result": json.dumps({"catalog_order": 6})}
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.tool_ok, res.reasons
        assert res.verdict.value == "CORRECT", res.reasons

    def test_article_lookup_fixture_has_no_unsupported_label_premise(self):
        raw_case = self._raw_case("product-lookup-hit-001")
        assert "меткой" not in raw_case["question"].lower()
        assert "ХИТ" not in raw_case["question"]
        assert "hit" not in raw_case["tags"]
        assert "article" in raw_case["tags"]

    def test_denso_fixture_accepts_grounded_describe_path(self):
        raw_case = self._raw_case("brand-lookup-002")
        assert "db_describe" in raw_case["expected_tool"]["must_call_any"]
        case = TestCase.from_dict(raw_case)
        run = make_run(
            final_text="Denso из Японии.",
            tool_calls=[
                {"name": "db_describe", "arguments": {"entity": "catalog_brand"}}
            ],
            tool_results=[
                {
                    "name": "db_describe",
                    "result": json.dumps(
                        {
                            "fields": {
                                "country": {"distinct": ["Япония"]},
                                "description": {"distinct": ["Denso OEM из Япония."]},
                            }
                        }
                    ),
                }
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.tool_ok, res.reasons

    def test_absence_fixture_accepts_brand_first_lookup_path(self):
        raw_case = self._raw_case("product-absence-003")
        assert "filter_catalog_brand" in raw_case["expected_tool"]["must_call_any"]
        case = TestCase.from_dict(raw_case)
        run = make_run(
            final_text="Brand is not found.",
            tool_calls=[
                {
                    "name": "filter_catalog_brand",
                    "arguments": {"name": "MISSING"},
                }
            ],
            tool_results=[
                {
                    "name": "filter_catalog_brand",
                    "result": json.dumps({"total": 0, "returned": 0}),
                }
            ],
        )
        res = DeterministicEvaluator().evaluate(case, run)
        assert res.tool_ok, res.reasons
        assert res.verdict.value == "CORRECT", res.reasons
