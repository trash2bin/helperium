"""MCP-сервер университетского ассистента.

Данные университета — через data-service (Go) по HTTP.
RAG-документы — через RAG-сервис по HTTP.
SQL-запросов университетских данных не содержит.
"""

import os
import logging
from mcp.server.fastmcp import FastMCP
from typing import Annotated, Any, Optional, List
from pydantic import Field

from mcp_server.tools_via_http import (  # университетские данные → data-service (Go)
    _find_student_by_name,
    _get_student,
    _get_schedule,
    _get_disciplines,
    _get_student_grades,
    _get_teacher_by_name,
    _get_teacher_schedule,
    _health_db_status,
)
from mcp_server.tools_rag import (  # RAG-документы → RAG-сервис
    init_rag,
    _list_documents,
    _search_documents,
    _context_search_in_documents,
    _get_health_status_rag,
)

logger = logging.getLogger(__name__)

mcp = FastMCP("University Server")


# ══════════════════════════════════════════════════════════════════════
# СТУДЕНТ
# ══════════════════════════════════════════════════════════════════════


@mcp.tool()
async def find_student_by_name(
    name: Annotated[
        str, Field(description="Полное ФИО студента. Пример: 'Иван Петров Иванович'")
    ],
) -> Optional[Any]:
    """Найти студента по имени."""
    return await _find_student_by_name(name)


@mcp.tool()
async def get_student(
    student_id: Annotated[
        str,
        Field(
            description="ID студента (UUID или число). Получи через find_student_by_name."
        ),
    ],
) -> Optional[Any]:
    """Получить карточку студента по ID."""
    return await _get_student(student_id)


# ══════════════════════════════════════════════════════════════════════
# РАСПИСАНИЕ
# ══════════════════════════════════════════════════════════════════════


@mcp.tool()
async def get_schedule(
    group_id: Annotated[
        str, Field(description="ID группы (UUID). Берётся из поля group.id студента.")
    ],
    day: Annotated[
        Optional[str],
        Field(
            description="День недели по-русски. Не передавай если нужно всё расписание."
        ),
    ] = None,
) -> List[Any]:
    """Расписание группы студента."""
    return await _get_schedule(group_id, day)


# ══════════════════════════════════════════════════════════════════════
# ДИСЦИПЛИНЫ И ОЦЕНКИ
# ══════════════════════════════════════════════════════════════════════


@mcp.tool()
async def get_disciplines(
    student_id: Annotated[
        str, Field(description="ID студента из find_student_by_name или get_student.")
    ],
) -> List[Any]:
    """Список дисциплин студента."""
    return await _get_disciplines(student_id)


@mcp.tool()
async def get_student_grades(
    student_id: Annotated[
        str, Field(description="ID студента из find_student_by_name.")
    ],
    discipline_id: Annotated[
        Optional[str],
        Field(
            description="ID дисциплины для фильтрации. Не передавай если нужны все оценки."
        ),
    ] = None,
) -> List[Any]:
    """Оценки студента."""
    return await _get_student_grades(student_id, discipline_id)


# ══════════════════════════════════════════════════════════════════════
# ПРЕПОДАВАТЕЛЬ
# ══════════════════════════════════════════════════════════════════════


@mcp.tool()
async def get_teacher_by_name(
    name: Annotated[str, Field(description="Полное ФИО преподавателя.")],
) -> Optional[Any]:
    """Найти преподавателя по имени."""
    return await _get_teacher_by_name(name)


@mcp.tool()
async def get_teacher_schedule(
    teacher_name: Annotated[str, Field(description="Полное ФИО преподавателя.")],
    day: Annotated[
        Optional[str],
        Field(
            description="День недели по-русски. Не передавай если нужно всё расписание."
        ),
    ] = None,
) -> List[Any]:
    """Расписание преподавателя."""
    return await _get_teacher_schedule(teacher_name, day)


# ══════════════════════════════════════════════════════════════════════
# ДОКУМЕНТЫ / RAG
# ══════════════════════════════════════════════════════════════════════


@mcp.tool()
async def list_documents(
    discipline_id: Annotated[
        Optional[str],
        Field(description="ID дисциплины для фильтрации."),
    ] = None,
    limit: Annotated[
        Optional[int], Field(description="Максимум документов (1–1000).", ge=1, le=1000)
    ] = None,
) -> List[Any]:
    """Список документов, доступных для RAG-поиска."""
    return await _list_documents(discipline_id, limit)


@mcp.tool()
async def search_documents(
    query: Annotated[str, Field(description="Поисковый запрос по документам.")],
    discipline_id: Annotated[
        Optional[str], Field(description="ID дисциплины для сужения поиска.")
    ] = None,
    limit: Annotated[
        int, Field(description="Количество фрагментов (1–20).", ge=1, le=20)
    ] = 5,
) -> List[Any]:
    """Поиск релевантных фрагментов документов (RAG)."""
    return await _search_documents(query, discipline_id, limit)


@mcp.tool()
async def context_search_in_documents(
    query: Annotated[
        str, Field(description="Вопрос пользователя для поиска по документам.")
    ],
    discipline_id: Annotated[
        Optional[str], Field(description="ID дисциплины для сужения контекста.")
    ] = None,
    limit: Annotated[
        int, Field(description="Фрагментов в контексте (1–20).", ge=1, le=20)
    ] = 5,
) -> Any:
    """Готовый RAG-контекст для ответа модели."""
    return await _context_search_in_documents(query, discipline_id, limit)


# ══════════════════════════════════════════════════════════════════════
# СЛУЖЕБНОЕ
# ══════════════════════════════════════════════════════════════════════


@mcp.tool()
async def get_health_status() -> dict:
    """Проверить работоспособность системы (data-service, RAG)."""
    db_status = await _health_db_status()
    rag_status = await _get_health_status_rag()
    return {"database": db_status, "rag": rag_status}


# ══════════════════════════════════════════════════════════════════════
# Запуск
# ══════════════════════════════════════════════════════════════════════


def main():
    """Запустить MCP-сервер с HTTP-транспортом и health endpoint'ом."""
    init_rag()

    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def health(request):
        status = await get_health_status()
        overall = all(v.get("status") == "ok" for v in status.values())
        return JSONResponse(
            {"status": "ok" if overall else "degraded", **status},
            status_code=200 if overall else 503,
        )

    app = mcp.streamable_http_app()
    app.routes.append(Route("/health", endpoint=health))

    import uvicorn

    port = int(os.environ.get("MCP_PORT", "8083"))
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
