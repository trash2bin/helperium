import os
from mcp.server.fastmcp import FastMCP
from typing import Annotated
from pydantic import Field
from db.database import Database
from db.models import (
    Document,
    Grade,
    Material,
    RagContext,
    RagSearchResult,
    ScheduleEntry,
    Discipline,
    Student,
    Teacher,
)
from tools.student import StudentTools
from tools.disciplines import DisciplineTools
from tools.grades import GradeTools
from tools.teacher import TeacherTools
from tools.rag import RagTools
from tools.document_generator import MaterialDocumentGenerator


db = Database()
student_tools = StudentTools(db)
grade_tools = GradeTools(db)
teacher_tools = TeacherTools(db)
rag_tools = RagTools(db)
material_generator = MaterialDocumentGenerator(db, rag_tools)
discipline_tools = DisciplineTools(db, material_generator)

mcp = FastMCP("University Server")


@mcp.tool()
def get_student(
    student_id: Annotated[str, Field(description="Числовой ID студента, например '1' или '42' или может быть uuid4 id например '3fa85f64-5717-4562-b3fc-2c963f66afa6'")]
) -> Student | None:
    """
    Получить карточку студента по его ID.
    Возвращает имя, группу, курс и специальность.
    Если студент не найден — возвращает null.
    Чтобы найти ID по имени — используй find_student_by_name.
    """
    return student_tools.get_student(student_id)


@mcp.tool()
def find_student_by_name(
    name: Annotated[str, Field(description="Полное имя студента, например 'Иван Петров Иванович'")]
) -> Student | None:
    """
    Найти студента по имени и получить его ID и данные.
    Используй этот инструмент первым, если знаешь имя но не знаешь ID.
    После получения ID можно вызывать get_disciplines, get_schedule и другие инструменты.
    Возвращает null если студент не найден.
    """
    return student_tools.get_id_student(name)


@mcp.tool()
def get_schedule(
    group_id: Annotated[str, Field(description="ID группы, например uuid4 '123e4567-e89b-12d3-a456-426614174000'")],
    day: Annotated[str | None, Field(description="День недели на русском: 'Понедельник', 'Вторник' и т.д. Если не указан — вернётся всё расписание группы")] = None
) -> list[ScheduleEntry]:
    """
    Получить расписание группы.
    Название группы есть в карточке студента (поле group).
    Можно фильтровать по конкретному дню недели.
    """
    return student_tools.get_schedule(group_id, day)


@mcp.tool()
def get_disciplines(
    student_id: Annotated[str, Field(description="ID студента из get_student или find_student_by_name")]
) -> list[Discipline]:
    """
    Получить список дисциплин студента.
    Дисциплины определяются через расписание его группы.
    Возвращает id и название каждой дисциплины — id нужен для get_materials.
    """
    return discipline_tools.get_disciplines(student_id)


@mcp.tool()
def get_materials(
    discipline_id: Annotated[str, Field(description="ID дисциплины из get_disciplines")],
    material_type: Annotated[str | None, Field(description="Тип материала: 'Лекция', 'Методичка', 'Лабораторная работа'. Если не указан — вернутся все типы")] = None
) -> list[Material]:
    """
    Получить учебные материалы по дисциплине.
    Если для дисциплины еще нет файлов, сервер локально сгенерирует PDF/DOCX,
    привяжет их к дисциплине и вернет названия файлов.
    ID дисциплины можно получить через get_disciplines.
    """
    return discipline_tools.get_materials(discipline_id, material_type)


@mcp.tool()
def generate_materials(
    discipline_id: Annotated[str, Field(description="ID дисциплины из get_disciplines")],
    force: Annotated[bool, Field(description="true — пересоздать файлы даже если они уже есть; false — вернуть существующие")] = False,
) -> list[Material]:
    """
    Сгенерировать реальные PDF/DOCX-файлы для дисциплины и привязать их к ней.
    Возвращает материалы как список файлов: лекция, методичка, лабораторная работа.
    """
    return discipline_tools.generate_materials(discipline_id, force=force)


@mcp.tool()
def search_materials(
    query: Annotated[str, Field(description="Поисковый запрос, ищется по содержимому материалов")],
    discipline_id: Annotated[str | None, Field(description="Опциональный ID дисциплины для сужения поиска")] = None
) -> list[Material]:
    """
    Найти учебные материалы по содержимому.
    Можно искать по всем дисциплинам сразу или ограничить одной.
    """
    return discipline_tools.search_materials(query, discipline_id)


@mcp.tool()
def get_student_grades(
    student_id: Annotated[str, Field(description="ID студента из get_student или find_student_by_name")],
    discipline_id: Annotated[str | None, Field(description="Опциональный ID дисциплины из get_disciplines, если нужно получить оценки только по одному предмету")] = None
) -> list[Grade]:
    """
    Получить все оценки студента.
    Возвращает дату, саму оценку и название дисциплины.
    Можно отфильтровать результат по конкретной дисциплине.
    """
    return grade_tools.get_student_grades(student_id, discipline_id)

@mcp.tool()
def get_teacher_by_name(
    name: Annotated[str, Field(description="Имя учителя, например 'Оксана Ниловна Константинова'")]
) -> Teacher | None:
    """
    Найти учителя по имени.
    """
    return teacher_tools.get_teacher_by_name(name)


@mcp.tool()
def get_teacher_schedule(
    teacher_name: Annotated[str, Field(description="Имя учителя")],
    day: Annotated[str | None, Field(description="День недели (по умолчанию - текущий день)")] = None
) -> list[ScheduleEntry]:
    """
    Получить расписание учителя.
    """
    return teacher_tools.get_teacher_schedule(teacher_name, day)


@mcp.tool()
def list_documents(
    discipline_id: Annotated[str | None, Field(description="Опциональный ID дисциплины для фильтрации документов")] = None
) -> list[Document]:
    """
    Получить список документов, доступных RAG-поиску.
    """
    return rag_tools.list_documents(discipline_id)


@mcp.tool()
def search_documents(
    query: Annotated[str, Field(description="Вопрос или поисковый запрос по загруженным документам")],
    discipline_id: Annotated[str | None, Field(description="Опциональный ID дисциплины для сужения поиска")] = None,
    limit: Annotated[int, Field(description="Сколько релевантных фрагментов вернуть, от 1 до 20")] = 5,
) -> list[RagSearchResult]:
    """
    Найти релевантные фрагменты документов через локальный RAG-поиск.
    Возвращает текст фрагмента, оценку релевантности и источник.
    """
    return rag_tools.search_documents(query, discipline_id, limit)


@mcp.tool()
def get_rag_context(
    query: Annotated[str, Field(description="Вопрос пользователя, на который нужно ответить по документам")],
    discipline_id: Annotated[str | None, Field(description="Опциональный ID дисциплины для сужения контекста")] = None,
    limit: Annotated[int, Field(description="Сколько фрагментов включить в контекст, от 1 до 20")] = 5,
) -> RagContext:
    """
    Получить готовый RAG-контекст для ответа модели.
    Модель должна отвечать только по возвращённым фрагментам и явно говорить, если данных недостаточно.
    """
    return rag_tools.build_rag_context(query, discipline_id, limit)


def main():
    mcp.run()

if __name__ == "__main__":
    main()
