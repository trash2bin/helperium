"""Benchmark data structures."""

from dataclasses import dataclass, field
from typing import Any


DEFAULT_BENCH_QUESTIONS = [
    "Нужен глушитель на BMW X5",
    "Покажи тормозные колодки",
    "Сколько стоит замена масла?",
    "Есть ли в наличии шины R17?",
    "Найди запчасти для Mercedes W204",
    "Какие бренды тормозных систем есть?",
    'Покажи всё из раздела "Двигатель"',
    "Нужен стартер на Toyota Camry 2018",
]


@dataclass
class BenchQuestion:
    """A single benchmark question."""

    text: str


@dataclass
class ToolCallEvent:
    """One tool call within a turn."""

    name: str
    arguments: dict[str, Any]
    result: str | None = None
    result_chars: int = 0
    duration_ms: float = 0.0


@dataclass
class TurnResult:
    """Aggregated result for one question turn."""

    session_id: str
    turn_id: str
    question: str
    outcome: str  # final | error | limit
    duration_ms: float
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    total_cost: float
    llm_calls: int
    tool_calls: list[ToolCallEvent] = field(default_factory=list)
    tool_errors: int = 0
    empty_results: int = 0
    empty_rounds: int = 0
    iterations: int = 0
    final_text: str = ""
    errors: list[str] = field(default_factory=list)
    loop_warnings: list[str] = field(default_factory=list)


@dataclass
class BenchReport:
    """Complete benchmark report (legacy: from backlog parsing)."""

    turns: list[TurnResult]
    total_questions: int = 0
    passed: int = 0
    failed: int = 0
    errored: int = 0
    total_cost: float = 0.0
    total_duration_ms: float = 0.0
    total_tokens: int = 0


# ═══════════════════════════════════════════════════════════════════════════
# Core benchmark dataclasses (ТЗ: Core Benchmark)
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class TestCase:
    """A single benchmark case from cases/autoparts.json."""

    # pytest: not a test class
    __test__ = False

    id: str
    question: str
    category: str
    ground_truth: dict[str, Any]
    expected_tool: dict[str, Any] | None = None
    status_synonyms: dict[str, list[str]] | None = None
    tags: list[str] = field(default_factory=list)
    expect_refusal: bool = False

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TestCase":
        return cls(
            id=d["id"],
            question=d["question"],
            category=d.get("category", ""),
            ground_truth=d.get("ground_truth", {}),
            expected_tool=d.get("expected_tool"),
            status_synonyms=d.get("status_synonyms"),
            tags=d.get("tags", []),
            expect_refusal=d.get("expect_refusal", False),
        )


@dataclass
class BacklogData:
    """Metrics extracted from the backlog turn_end record."""

    duration_ms: float = 0.0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0
    llm_calls: int = 0
    tool_calls_count: int = 0
    tool_errors: int = 0
    empty_results: int = 0
    empty_rounds: int = 0
    iterations: int = 0
    outcome: str = "final"

    @classmethod
    def from_turn_end(cls, rec: dict[str, Any]) -> "BacklogData":
        return cls(
            duration_ms=rec.get("duration_ms", 0.0) or 0.0,
            total_prompt_tokens=rec.get("total_prompt_tokens", 0) or 0,
            total_completion_tokens=rec.get("total_completion_tokens", 0) or 0,
            total_tokens=rec.get("total_tokens", 0) or 0,
            total_cost=rec.get("total_cost", 0.0) or 0.0,
            llm_calls=rec.get("llm_calls", 0) or 0,
            tool_calls_count=rec.get("tool_calls", 0) or 0,
            tool_errors=rec.get("tool_errors", 0) or 0,
            empty_results=rec.get("empty_results", 0) or 0,
            empty_rounds=rec.get("empty_rounds", 0) or 0,
            iterations=rec.get("iterations", 0) or 0,
            outcome=rec.get("outcome", "final"),
        )


@dataclass
class RunResult:
    """Result of running one case against the API (SSE + backlog)."""

    session_id: str
    question: str
    final_text: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    status_messages: list[str] = field(default_factory=list)
    backlog: BacklogData | None = None


@dataclass
class EvalResult:
    """Deterministic evaluation verdict for one case."""

    case_id: str
    tool_ok: bool = False
    retrieval_ok: bool = False
    answer_ok: bool = False
    hallucination: bool = False
    grounded: bool = False
    refusal_ok: bool = True
    entity_name_ok: bool = True
    reasons: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """Case passes when data was retrieved, answered correctly, and no hallucination."""
        return self.retrieval_ok and self.answer_ok and not self.hallucination


@dataclass
class CaseResult:
    """Full result for one case: evaluation + backlog metrics."""

    case_id: str
    question: str
    category: str = ""
    tags: list[str] = field(default_factory=list)
    eval_result: EvalResult | None = None
    duration_ms: float = 0.0
    total_tokens: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    iterations: int = 0
    cost_usd: float = 0.0
    outcome: str = "final"
    final_text: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return bool(self.eval_result and self.eval_result.success)


@dataclass
class BenchmarkReport:
    """Aggregated metrics across all cases."""

    total_cases: int = 0
    success_rate: float = 0.0
    retrieval_success_rate: float = 0.0
    answer_delivery_rate: float = 0.0
    hallucination_rate: float = 0.0
    groundedness_rate: float = 0.0
    refusal_correct_rate: float = 0.0
    tool_error_rate: float = 0.0
    entity_name_accuracy: float = 0.0
    recovery_rate: float = 0.0
    avg_total_tokens: float = 0.0
    avg_prompt_tokens: float = 0.0
    avg_completion_tokens: float = 0.0
    avg_llm_calls: float = 0.0
    avg_tool_calls: float = 0.0
    avg_iterations: float = 0.0
    avg_duration_ms: float = 0.0
    p95_duration_ms: float = 0.0
    avg_cost_usd: float = 0.0
    total_cost_usd: float = 0.0
    total_duration_wall_ms: float = 0.0
    case_results: list[CaseResult] = field(default_factory=list)

    # Internal counters (aggregation)
    success_count: int = 0
    retrieval_count: int = 0
    answer_count: int = 0
    hallucination_count: int = 0
    grounded_count: int = 0
    refusal_count: int = 0
    tool_error_turns: int = 0
    entity_name_ok_count: int = 0
    errors_but_final_count: int = 0
    errors_total_count: int = 0
    avg_prompt_tokens_sum: float = 0.0
    avg_completion_tokens_sum: float = 0.0
    avg_total_tokens_sum: float = 0.0
    avg_llm_calls_sum: float = 0.0
    avg_tool_calls_sum: float = 0.0
    avg_iterations_sum: float = 0.0
    avg_cost_usd_sum: float = 0.0
    avg_duration_sum: float = 0.0
