from typing import Optional
from db.database import Database
from db.models import Student, ScheduleEntry

class StudentTools:
    def __init__(self, db: Database):
        self.db = db

    def get_student(self, student_id: str) -> Optional[Student]:
        """Get student information by ID"""
        return self.db.get_student(student_id)

    def get_id_student(self, name: str) -> Optional[Student]:
        """Get student information by name"""
        return self.db.get_id_student(name)

    def get_schedule(self, group_id: str, week: str | None = None) -> list[ScheduleEntry]:
        """Get schedule for a group"""
        return self.db.get_schedule(group_id, week)
