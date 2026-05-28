from typing import List
from db.database import Database
from db.models import Discipline, Material

class DisciplineTools:
    def __init__(self, db: Database):
        self.db = db

    def get_disciplines(self, student_id: str) -> List[Discipline]:
        """Get disciplines for a student"""
        return self.db.get_disciplines(student_id)

    def get_materials(self, discipline_id: str, material_type: str | None = None) -> List[Material]:
        """Get document materials for a discipline"""
        return self.db.get_materials(discipline_id, material_type)

    def search_materials(self, query: str, discipline_id: str | None = None) -> List[Material]:
        """Search document materials by title or indexed content"""
        return self.db.search_materials(query, discipline_id)
