import os
from mcp.server.fastmcp import FastMCP
from typing import Annotated, Optional, List
from pydantic import Field
from db.database import Database
from db.models import (
    Grade,
    Material,
    ScheduleEntry,
    Discipline,
    Student,
    Teacher,
)
from tools.student import StudentTools
from tools.disciplines import DisciplineTools
from tools.grades import GradeTools
from tools.teacher import TeacherTools
from rag.client import RagClient, RAG_SERVICE_URL
from rag.models import Document, RagContext, RagSearchResult


# Инициализация инструментов БД
db = Database()
student_tools = StudentTools(db)
grade_tools = GradeTools(db)
teacher_tools = TeacherTools(db)
discipline_tools = DisciplineTools(db)

# HTTP-клиент к RAG-сервису
rag_client = RagClient(RAG_SERVICE_URL)

mcp = FastMCP("University Server")


# СТУДЕНТ

@mcp.tool()
def find_student_by_name(
    name: Annotated[str, Field(description="Полное ФИО студента. Пример: 'Иван Петров Иванович'")]
) -> Optional[Student]:
    """Найти студента по имени.

    Используй ПЕРВЫМ если знаешь имя, но не знаешь ID.
    Возвращает Student: id, name, course, group {id, name, speciality}.
    group.id нужен для get_schedule. id нужен для get_student_grades и get_disciplines.
    Возвращает null если не найден.
    """
    return student_tools.get_id_student(name)


@mcp.tool()
def get_student(
    student_id: Annotated[str, Field(description="ID студента (UUID или число). Получи через find_student_by_name.")]
) -> Optional[Student]:
    """Получить карточку студента по ID.

    Возвращает Student: id, name, course, group {id, name, speciality}.
    Возвращает null если не найден.
    """
    return student_tools.get_student(student_id)


# РАСПИСАНИЕ

@mcp.tool()
def get_schedule(
    group_id: Annotated[str, Field(description="ID группы (UUID). Берётся из поля group.id студента.")],
    day: Annotated[Optional[str], Field(
        description="День недели по-русски: 'Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота'. "
                    "Не передавай если нужно всё расписание."
    )] = None
) -> List[ScheduleEntry]:
    """Расписание группы студента.

    ВАЖНО: принимает group.id (из карточки студента), а НЕ student.id.
    Возвращает список ScheduleEntry: day, group, lessons[{discipline_id, discipline_name, teacher_name, room}].
    """
    return student_tools.get_schedule(group_id, day) or []


# ДИСЦИПЛИНЫ И ОЦЕНКИ

@mcp.tool()
def get_disciplines(
    student_id: Annotated[str, Field(description="ID студента из find_student_by_name или get_student.")]
) -> List[Discipline]:
    """Список дисциплин студента.

    Возвращает List[Discipline]: id, name, description.
    discipline.id можно передать в get_student_grades для фильтрации по предмету.
    """
    return discipline_tools.get_disciplines(student_id) or []


@mcp.tool()
def get_student_grades(
    student_id: Annotated[str, Field(description="ID студента из find_student_by_name.")],
    discipline_id: Annotated[Optional[str], Field(
        description="ID дисциплины из get_disciplines для фильтрации по предмету. "
                    "Не передавай если нужны все оценки — вызов без discipline_id вернёт их все."
    )] = None
) -> List[Grade]:
    """Оценки студента.

    Без discipline_id — все оценки. С discipline_id — только по этому предмету.
    ВАЖНО: не перебирай discipline_id вручную — вызови один раз без него, чтобы получить всё.
    Возвращает List[Grade]: id, student_id, discipline_id, discipline_name, grade, date.
    """
    return grade_tools.get_student_grades(student_id, discipline_id) or []


# ПРЕПОДАВАТЕЛЬ

@mcp.tool()
def get_teacher_by_name(
    name: Annotated[str, Field(description="Полное ФИО преподавателя. Пример: 'Оксана Ниловна Константинова'")]
) -> Optional[Teacher]:
    """Найти преподавателя по имени.

    Возвращает Teacher: id, name, disciplines[]. Null если не найден.
    """
    return teacher_tools.get_teacher_by_name(name)


@mcp.tool()
def get_teacher_schedule(
    teacher_name: Annotated[str, Field(description="Полное ФИО преподавателя.")],
    day: Annotated[Optional[str], Field(
        description="День недели по-русски. Не передавай если нужно всё расписание."
    )] = None
) -> List[ScheduleEntry]:
    """Расписание преподавателя.

    Принимает имя напрямую, отдельный вызов get_teacher_by_name не нужен.
    Возвращает List[ScheduleEntry]: day, group, lessons[].
    """
    return teacher_tools.get_teacher_schedule(teacher_name, day) or []


# ДОКУМЕНТЫ / RAG

@mcp.tool()
def list_documents(
    discipline_id: Annotated[Optional[str], Field(
        description="ID дисциплины для фильтрации. Не передавай для получения всех документов."
    )] = None,
    limit: Annotated[Optional[int], Field(description="Максимум документов (1–1000).", ge=1, le=1000)] = None
) -> List[Document]:
    """Список документов, доступных для RAG-поиска.

    Возвращает List[Document]: id, title, source_path, mime_type, discipline_id, created_at.
    """
    return rag_client.list_documents_sync(discipline_id, limit) or []


@mcp.tool()
def search_documents(
    query: Annotated[str, Field(description="Поисковый запрос по документам.")],
    discipline_id: Annotated[Optional[str], Field(
        description="ID дисциплины для сужения поиска. Опционально."
    )] = None,
    limit: Annotated[int, Field(description="Количество фрагментов (1–20).", ge=1, le=20)] = 5,
) -> List[RagSearchResult]:
    """Поиск релевантных фрагментов документов (RAG).

    Возвращает List[RagSearchResult]: document_id, document_title, chunk_id, page, score, content.
    Используй context_search_in_documents если нужен готовый контекст для ответа.
    """
    return rag_client.search_documents_sync(query, discipline_id, limit) or []


@mcp.tool()
def context_search_in_documents(
    query: Annotated[str, Field(description="Вопрос пользователя для поиска по документам.")],
    discipline_id: Annotated[Optional[str], Field(
        description="ID дисциплины для сужения контекста. Опционально."
    )] = None,
    limit: Annotated[int, Field(description="Фрагментов в контексте (1–20).", ge=1, le=20)] = 5,
) -> RagContext:
    """Готовый RAG-контекст для ответа модели.

    Предпочитай этот инструмент вместо search_documents когда нужно ответить на вопрос по материалам.
    Возвращает RagContext: query, answer_instruction, chunks[].
    Отвечай только на основе chunks, явно укажи если данных недостаточно.
    """
    return rag_client.build_rag_context_sync(query, discipline_id, limit)


# СЛУЖЕБНОЕ

@mcp.tool()
def get_health_status() -> dict:
    """Проверить работоспособность системы (БД и RAG-сервис).

    Возвращает {"database": {"status": "ok"|"error", "error": null|str},
                "rag": {"status": "ok"|"error", "error": null|str}}.
    """
    db_status = {"status": "ok", "error": None}
    try:
        db.ping()
    except Exception as e:
        db_status = {"status": "error", "error": str(e)}

    rag_status = {"status": "ok", "error": None}
    try:
        health = rag_client.health_sync()
        if health.get("status") != "ok":
            rag_status = {"status": "error", "error": "RAG service degraded"}
    except Exception as e:
        rag_status = {"status": "error", "error": str(e)}

    return {"database": db_status, "rag": rag_status}


def main():
    mcp.run(transport="streamable-http", mount_path="/mcp")


if __name__ == "__main__":
    main()
