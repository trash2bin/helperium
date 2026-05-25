from pydantic import BaseModel
from typing import List, Optional

class Student(BaseModel):
    id: str
    name: str
    group: str
    course: int
    specialty: str

class Discipline(BaseModel):
    id: str
    name: str
    description: str

class Material(BaseModel):
    id: str
    discipline_id: str
    type: str
    content: str

class Lesson(BaseModel):
    discipline_id: str
    discipline_name: str
    room: int

class ScheduleEntry(BaseModel):
    id: str
    group: str
    day: str
    lessons: List[Lesson]

class CoursePlan(BaseModel):
    id: str
    student_id: str
    discipline_id: str
    topics: List[str]
    weeks: List[str]
    materials: List[str]
