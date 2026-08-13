"""Derived number detection — numbers calculated from tool results, not hallucinations.

Extracted from DeterministicEvaluator for testability and clarity.
"""

from __future__ import annotations

import re
from typing import Any


class DerivedNumberDetector:
    """Detects numbers in answer that are derived from tool_results (not hallucinations).

    Two detection modes:
    1. Arithmetic context in answer text ("677×3=2031", "плюс ещё 5", "итого 7809")
    2. Mathematical derivation from tool numbers (products/sums with small multiplier)
    """

    # Small multipliers (1..9) — line-item quantities, not arbitrary numbers
    SMALL_MULTIPLIERS = frozenset(range(1, 10))

    # Markers that indicate explicit arithmetic in answer text
    ARITHMETIC_MARKERS = ("×", "*", "\u00d7", "\u2212", "+", "-", "=")
    ADDITION_MARKERS = ("ещё", "еще", "плюс", "итого", "суммарно")

    # Words that do NOT indicate arithmetic (descriptive only)
    DESCRIPTIVE_MARKERS = ("всего", "товаров", "позиций", "строк", "штук")

    def __init__(
        self,
        max_multiplier: int = 9,
        max_sum_terms: int = 3,
        max_product: int = 1_000_000,
    ) -> None:
        self.max_multiplier = max_multiplier
        self.max_sum_terms = max_sum_terms
        self.max_product = max_product

    # ═══════════════════════════════════════════════════════════════════════
    # Public API
    # ═══════════════════════════════════════════════════════════════════════

    def find_derived_from_text(self, answer_text: str) -> set[str]:
        """Find numbers in answer that appear in arithmetic context.

        Returns set of number strings that are explicitly calculated in the answer.
        """
        derived: set[str] = set()
        s = str(answer_text)

        # Pattern 1: Arithmetic expressions with "=" — "677×3=2031", "677 × 3 позиции = 2031"
        arith_pattern = re.compile(
            r"\d[\d\s\u00a0]*(?:\s*(?:[×*+−-]|\u00d7|\u2212)\s*[\w\s\u00a0]*\d[\d\s\u00a0]*)+"
            r"\s*[\w\s\u00a0]*=\s*\d+"
        )
        for m in arith_pattern.finditer(s):
            for n in re.findall(r"\d+", m.group(0)):
                if len(n) >= 2:
                    derived.add(n)

        # Pattern 2: Explicit addition/total markers — "ещё 5", "плюс 10", "итого 7809"
        # Only these markers forgive a number without "="; descriptive words don't.
        for m in re.finditer(
            r"(?:ещё|еще|плюс|итого|суммарно)\s*(?:—|-|:)?\s*(\d+(?:[\s\u00a0]\d{3})*)",
            s,
            re.IGNORECASE,
        ):
            n = m.group(1).replace("\u00a0", "").replace(" ", "")
            if len(n) >= 2:
                derived.add(n)

        return derived

    def find_derived_from_tool_numbers(
        self,
        unsupported: list[str],
        tool_numbers: set[str],
    ) -> set[str]:
        """Find numbers expressible as product/sum of confirmed tool numbers.

        Rules:
        - Product: exactly ONE small multiplier (1..9) × confirmed big number
          (excludes 700 = 20×35 where both are big; keeps 2031 = 677×3)
        - Sum: 2-3 terms, at least ONE small (1..9) — counts of items/lines
        """
        # Extract confirmed numbers from tool_results (max 9 digits to avoid IDs)
        nums = sorted(
            int(n) for n in tool_numbers
            if n.isdigit() and len(n) <= 9
        )
        if not nums:
            return set()

        small = self.SMALL_MULTIPLIERS
        big_nums = [n for n in nums if n not in small]

        # Products: small × big (not small × small, not big × big)
        products: set[int] = set()
        for big in big_nums:
            for s in small:
                p = big * s
                if p <= self.max_product:
                    products.add(p)

        # Sums of 2 terms: at least one small
        sums: set[int] = set()
        for i in range(len(nums)):
            for j in range(i + 1, len(nums)):
                if nums[i] in small or nums[j] in small:
                    sums.add(nums[i] + nums[j])

        # Sums of 3 terms: at least one small
        if self.max_sum_terms >= 3:
            for i in range(len(nums)):
                for j in range(i + 1, len(nums)):
                    for k in range(j + 1, len(nums)):
                        if nums[i] in small or nums[j] in small or nums[k] in small:
                            sums.add(nums[i] + nums[j] + nums[k])

        # Check unsupported numbers against derived set
        derived: set[str] = set()
        for u in unsupported:
            if not u.isdigit():
                continue
            n = int(u)
            if n in products or n in sums:
                derived.add(u)
        return derived

    def find_all_derived(
        self,
        answer_text: str,
        tool_numbers: set[str],
        unsupported: list[str],
    ) -> set[str]:
        """Combine both detection modes."""
        derived = set()
        derived.update(self.find_derived_from_text(answer_text))
        derived.update(self.find_derived_from_tool_numbers(unsupported, tool_numbers))
        return derived


def extract_percents(text: str) -> set[str]:
    """Extract numbers next to '%' or 'процент' (model-calculated percentages)."""
    percents: set[str] = set()
    for m in re.finditer(r"(\d{1,3}(?:[\s\u00a0]\d{3})*)\s*%", str(text)):
        percents.add(m.group(1).replace("\u00a0", "").replace(" ", ""))
    for m in re.finditer(r"(\d{1,3})\s*процент", str(text), re.IGNORECASE):
        percents.add(m.group(1))
    return percents


def extract_row_numbers(text: str, max_row: int) -> set[str]:
    """Extract row/list numbers (1..max_row) from markdown tables/lists."""
    s = str(text)
    out: set[str] = set()

    # Build regex pattern for 1..max_row
    if max_row < 10:
        num_pattern = f"[1-{max_row}]"
    elif max_row < 100:
        num_pattern = f"[1-9]|[1-{max_row // 10}][0-9]|{max_row}"
    else:
        num_pattern = f"[1-9]|[1-9][0-9]|[1-9][0-9][0-9]"

    # Numbered list: "12. Товар" / "- 10. Товар" / "* 9. Товар"
    for m in re.finditer(rf"(?:^|\n)\s*(?:-\s*|\*\s*|\|\s*)?({num_pattern})\.\s+", s):
        out.add(m.group(1))
    # Table: "| 12 | Название |"
    for m in re.finditer(rf"\|\s*({num_pattern})\s*\|", s):
        out.add(m.group(1))
    return out


def max_row_from_tool_results(tool_results: list[dict[str, Any]]) -> int:
    """Derive max row number from tool_results (preview length or total)."""
    max_row = 50  # fallback
    for tr in tool_results:
        raw = tr.get("result", "")
        if isinstance(raw, str):
            try:
                obj = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue
        else:
            obj = raw
        if isinstance(obj, dict):
            for key in ("preview", "rows", "items", "data", "results"):
                if key in obj and isinstance(obj[key], list):
                    max_row = max(max_row, len(obj[key]))
            for key in ("total", "returned", "count"):
                if key in obj and isinstance(obj[key], (int, float)):
                    max_row = max(max_row, int(obj[key]))
    return max_row


import json  # at bottom to avoid circular import issues with type hints
