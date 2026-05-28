from typing import List, Optional
from db.database import Database
from db.models import Discipline, Material
from tools.document_generator import MaterialDocumentGenerator

class DisciplineTools:
    def __init__(self, db: Database, material_generator: MaterialDocumentGenerator | None = None):
        self.db = db
        self.material_generator = material_generator

    def get_disciplines(self, student_id: str) -> List[Discipline]:
        """Get disciplines for a student"""
        return self.db.get_disciplines(student_id)

    def get_materials(self, discipline_id: str, material_type: str | None = None) -> List[Material]:
        """Get generated document materials for a discipline"""
        if self.material_generator is not None:
            self.material_generator.ensure_materials(discipline_id)
        return self.db.get_materials(discipline_id, material_type)

    def generate_materials(self, discipline_id: str, force: bool = False) -> List[Material]:
        """Generate PDF/DOCX materials for a discipline and bind them to documents."""
        if self.material_generator is None:
            return self.db.get_materials(discipline_id)
        return self.material_generator.ensure_materials(discipline_id, force=force)

    def search_materials(self, query: str, discipline_id: str | None = None) -> List[Material]:
        """Search generated document materials by title or indexed content"""
        return self.db.search_materials(query, discipline_id)
