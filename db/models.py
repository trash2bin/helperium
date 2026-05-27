from pydantic import BaseModel
from typing import List, Optional

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

class Material(BaseModel):
    id: str
    discipline_id: str
    type: str
    content: str

class Document(BaseModel):
    id: str
    title: str
    source_path: str
    mime_type: str
    discipline_id: str | None = None
    created_at: str

class DocumentChunk(BaseModel):
    id: str
    document_id: str
    chunk_index: int
    page: int | None = None
    content: str

class DocumentImportResult(BaseModel):
    document: Document
    chunks_count: int

class RagSearchResult(BaseModel):
    document_id: str
    document_title: str
    source_path: str
    discipline_id: str | None = None
    chunk_id: str
    chunk_index: int
    page: int | None = None
    score: float
    content: str

class RagContext(BaseModel):
    query: str
    answer_instruction: str
    chunks: List[RagSearchResult]

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
