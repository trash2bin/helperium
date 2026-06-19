"""Инструменты для работы с дисциплинами и учебными материалами."""
from __future__ import annotations

from typing import Optional

from db.database import Database
from db.models import Discipline


class DisciplineTools:
    def __init__(self, db: Database):
        self.db = db

    def get_disciplines(self, student_id: str) -> list[Discipline]:
        """Get disciplines for a student."""
        return self.db.get_disciplines(student_id)
