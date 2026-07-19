"""Чистые утилиты для обработки текста."""

from __future__ import annotations

import re


TOKEN_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9]+")


def tokenize(text: str) -> list[str]:
    """Разбить текст на токены (простая регулярка)."""
    return [token.lower() for token in TOKEN_RE.findall(text)]


def normalize_text(text: str) -> str:
    """Нормализовать пробельные символы."""
    lines = [line.strip() for line in text.replace("\x00", " ").splitlines()]
    return re.sub(r"\s+", " ", " ".join(line for line in lines if line)).strip()
