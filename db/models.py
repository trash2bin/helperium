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

class ScheduleEntry(BaseModel):
    id: str
    group: str
    day: str
    time: str
    discipline_id: str
    room: int

class CoursePlan(BaseModel):
    id: str
    student_id: str
    discipline_id: str
    topics: List[str]
    weeks: List[str]
    materials: List[str]
