// Package models содержит доменные модели.
// jsonschema-теги используются для авто-генерации JSON Schema в specs/schemas/.
package models

// Group — учебная группа.
type Group struct {
	ID         string `json:"id" jsonschema:"description=Уникальный идентификатор группы"`
	Name       string `json:"name" jsonschema:"description=Название группы. Пример: ИВТ-21"`
	Speciality string `json:"speciality" jsonschema:"description=Специальность группы"`
}

// Student — карточка студента.
type Student struct {
	ID       string `json:"id" jsonschema:"description=Уникальный идентификатор студента"`
	FullName string `json:"full_name" jsonschema:"description=Полное ФИО студента"`
	Group    *Group `json:"group" jsonschema:"description=Группа студента. null если не назначена"`
	Course   *int   `json:"course" jsonschema:"description=Курс обучения (1–6). null если неизвестен"`
}

// Teacher — преподаватель.
type Teacher struct {
	ID          string   `json:"id" jsonschema:"description=Уникальный идентификатор преподавателя"`
	FullName    string   `json:"full_name" jsonschema:"description=Полное ФИО преподавателя"`
	Disciplines []string `json:"disciplines" jsonschema:"description=Список названий дисциплин"`
}

// Discipline — учебная дисциплина.
type Discipline struct {
	ID          string `json:"id" jsonschema:"description=Уникальный идентификатор дисциплины"`
	Name        string `json:"name" jsonschema:"description=Название дисциплины"`
	Description string `json:"description" jsonschema:"description=Краткое описание"`
}

// Grade — оценка студента.
type Grade struct {
	ID             string `json:"id" jsonschema:"description=Уникальный идентификатор записи"`
	StudentID      string `json:"student_id" jsonschema:"description=ID студента"`
	StudentName    string `json:"student_name" jsonschema:"description=Имя студента"`
	DisciplineID   string `json:"discipline_id" jsonschema:"description=ID дисциплины"`
	DisciplineName string `json:"discipline_name" jsonschema:"description=Название дисциплины"`
	Value          string `json:"grade" jsonschema:"description=Значение оценки: 5, 4, 3, 2, зачёт, незачёт"`
	Date           string `json:"date" jsonschema:"description=Дата в формате YYYY-MM-DD"`
}

// Lesson — одно занятие в расписании.
type Lesson struct {
	DisciplineID   string `json:"discipline_id" jsonschema:"description=ID дисциплины"`
	DisciplineName string `json:"discipline_name" jsonschema:"description=Название дисциплины"`
	TeacherName    string `json:"teacher_name" jsonschema:"description=ФИО преподавателя"`
	Room           int    `json:"room" jsonschema:"description=Номер аудитории"`
}

// ScheduleEntry — запись расписания на один день.
type ScheduleEntry struct {
	ID      string   `json:"id" jsonschema:"description=Уникальный идентификатор записи"`
	Group   *Group   `json:"group" jsonschema:"description=Группа"`
	Day     string   `json:"day" jsonschema:"description=День недели: Понедельник, Вторник, ..."`
	Lessons []Lesson `json:"lessons" jsonschema:"description=Список занятий"`
}
