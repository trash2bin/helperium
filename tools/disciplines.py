"""Инструменты для работы с дисциплинами и учебными материалами."""
from __future__ import annotations

from typing import Optional

from db.database import Database
from db.models import Discipline, Material

from rag.repository import DocumentRepository


class DisciplineTools:
    def __init__(self, db: Database, doc_repo: DocumentRepository | None = None):
        self.db = db
        self._doc_repo = doc_repo

    @property
    def doc_repo(self) -> DocumentRepository:
        if self._doc_repo is None:
            from rag.config import RagConfig
            self._doc_repo = DocumentRepository(self.db.conn, RagConfig.from_env())
        return self._doc_repo

    def get_disciplines(self, student_id: str) -> list[Discipline]:
        """Get disciplines for a student."""
        return self.db.get_disciplines(student_id)

    def get_materials(
        self, discipline_id: str, material_type: str | None = None
    ) -> list[Material]:
        """Get document materials for a discipline."""
        return self.doc_repo.get_materials(discipline_id, material_type)

    def search_materials(
        self, query: str, discipline_id: str | None = None
    ) -> list[Material]:
        """Search document materials by title or indexed content."""
        return self.doc_repo.search_materials(query, discipline_id)