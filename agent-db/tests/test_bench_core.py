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
    RunResult,
    TestCase,
)
from agent_db.bench.report import aggregate_report, print_report, report_to_dict
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
    return TestCase.from_dict({
        "id": cid,
        "question": kw.pop("question", "q"),
        "category": kw.pop("category", "lookup"),
        "ground_truth": {"type": gt_type, "expected": expected or {}},
        **kw,
    })


def make_run(final_text="", tool_calls=None, tool_results=None, **kw):
    return RunResult(
        session_id="s",
        question="q",
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
