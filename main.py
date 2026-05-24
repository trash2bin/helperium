from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class Student(BaseModel):
    id: str
    name: str
    group: str

class Discipline(BaseModel):
    id: str
    name: str
    description: str

students_db = {
    "1": Student(id="1", name="John Doe", group="CS101"),
    "2": Student(id="2", name="Jane Smith", group="CS102"),
}

disciplines_db = {
    "1": Discipline(id="1", name="Mathematics", description="Basic math concepts"),
    "2": Discipline(id="2", name="Physics", description="Basic physics concepts"),
}

@app.get("/student/{student_id}")
async def get_student(student_id: str):
    return students_db.get(student_id, {"error": "Student not found"})

@app.get("/schedule/{group_id}")
async def get_schedule(group_id: str, week: str | None = None):
    # Mock schedule data
    return {"group_id": group_id, "week": week, "schedule": ["Math", "Physics", "Chemistry"]}

@app.get("/disciplines/{student_id}")
async def get_disciplines(student_id: str):
    # Mock disciplines data
    return {"student_id": student_id, "disciplines": ["Mathematics", "Physics"]}

@app.get("/materials/{discipline_id}")
async def get_materials(discipline_id: str, material_type: str | None= None):
    # Mock materials data
    return {"discipline_id": discipline_id, "material_type": material_type, "materials": ["Lecture Notes", "Practice Problems"]}

@app.get("/search_materials")
async def search_materials(query: str, discipline_id: str | None = None):
    # Mock search results
    return {"query": query, "discipline_id": discipline_id, "results": ["Mathematics Lecture Notes", "Physics Practice Problems"]}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
