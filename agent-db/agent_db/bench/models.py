"""Pydantic models for benchmark data structures."""

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
    """Complete benchmark report."""

    turns: list[TurnResult]
    total_questions: int = 0
    passed: int = 0
    failed: int = 0
    errored: int = 0
    total_cost: float = 0.0
    total_duration_ms: float = 0.0
    total_tokens: int = 0
