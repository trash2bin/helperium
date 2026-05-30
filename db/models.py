from typing import List, Optional

from pydantic import BaseModel

# RAG-модели переехали в rag.models, реэкспортируем для обратной совместимости
from rag.models import (  # noqa: F401
    Document,
    DocumentChunk,
    DocumentImportResult,
    Material,
    RagContext,
    RagSearchResult,
)

class Group(BaseModel):
    id: str
    name: str
    speciality: str

class Student(BaseModel):
    id: str
    name: str
    group: Group | None
    course: int

class Teacher(BaseModel):
    id: str
    name: str
    disciplines: List[str]

class Discipline(BaseModel):
    id: str
    name: str
    description: str

class Grade(BaseModel):
    id: str
    student_id: str
    discipline_id: str
    discipline_name: str
    grade: str
    date: str

class Lesson(BaseModel):
    discipline_id: str
    discipline_name: str
    teacher_name: str
    room: int

class ScheduleEntry(BaseModel):
    id: str
    group: Group | None
    day: str
    lessons: List[Lesson]
