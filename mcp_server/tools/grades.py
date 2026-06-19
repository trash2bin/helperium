from db.database import Database
from db.models import Grade


class GradeTools:
    def __init__(self, db: Database):
        self.db = db

    def get_student_grades(self, student_id: str, discipline_id: str | None = None) -> list[Grade]:
        """Get all grades for a student, optionally filtered by discipline."""
        return self.db.get_student_grades(student_id, discipline_id)
