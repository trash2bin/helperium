from mcp.server.fastmcp import FastMCP
from db.database import Database
from db.models import Student, ScheduleEntry, Discipline, Material
from tools.student import StudentTools
from tools.disciplines import DisciplineTools

# Инициализация БД и инструментов
DB_PATH = "university.db"
db = Database(DB_PATH)
student_tools = StudentTools(db)
discipline_tools = DisciplineTools(db)

# Создание MCP-сервера
mcp = FastMCP("University Server")


@mcp.tool()
def get_student(student_id: str) -> Student | None:
    """Get student information by ID"""
    return student_tools.get_student(student_id)

@mcp.tool()
def get_id_student(name: str) -> Student | None:
    """Get student information by name"""
    return student_tools.get_id_student(name)

@mcp.tool()
def get_schedule(group_id: str, week: str | None = None) -> list[ScheduleEntry]:
    """Get schedule for a group"""
    return student_tools.get_schedule(group_id, week)


@mcp.tool()
def get_disciplines(student_id: str) -> list[Discipline]:
    """Get disciplines for a student"""
    return discipline_tools.get_disciplines(student_id)


@mcp.tool()
def get_materials(discipline_id: str, material_type: str | None = None) -> list[Material]:
    """Get materials for a discipline"""
    return discipline_tools.get_materials(discipline_id, material_type)


@mcp.tool()
def search_materials(query: str, discipline_id: str | None = None) -> list[Material]:
    """Search materials by content"""
    return discipline_tools.search_materials(query, discipline_id)


if __name__ == "__main__":
    # mcp.run(transport="streamable-http") # для тестирования
    mcp.run()
