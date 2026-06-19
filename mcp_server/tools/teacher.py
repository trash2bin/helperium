from typing import Optional, List
from db.database import Database
from db.models import Teacher, ScheduleEntry

class TeacherTools:
    def __init__(self, db: Database):
        self.db = db

    def get_teacher_by_name(self, name: str) -> Optional[Teacher]:
        """Поиск учителя по имени"""
        return self.db.get_teacher_by_name(name)

    def get_teacher_schedule(self, teacher_name: str, day: Optional[str] = None) -> List[ScheduleEntry]:
        """Получение расписания учителя"""
        return self.db.get_teacher_schedule(teacher_name, day)
