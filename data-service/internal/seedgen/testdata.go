package seedgen

// TestSeed — детерминированный набор данных для in-memory тестов.
// ID/имена выбраны так, чтобы тесты в internal/server могли проверять
// конкретные значения без зависимости от faker.
var TestSeed = &Seed{
	Groups: []Group{
		{ID: "g1", Name: "ИВТ-21", Speciality: "Информационные системы и технологии"},
		{ID: "g2", Name: "ПИ-20", Speciality: "Программная инженерия"},
	},
	Disciplines: []Discipline{
		{ID: "d1", Name: "Алгоритмы и структуры данных", Description: "Базы"},
		{ID: "d2", Name: "Базы данных", Description: "Реляционные"},
		{ID: "d3", Name: "Веб-технологии", Description: "HTTP"},
	},
	Teachers: []Teacher{
		{
			ID:          "t1",
			Name:        "Оксана Ниловна Константинова",
			Disciplines: []string{"Базы данных", "Веб-технологии"},
		},
	},
	Students: []Student{
		{ID: "s1", Name: "Иван Петров Иванович", GroupID: "g1", Course: 2},
		{ID: "s2", Name: "Мария Сидорова Ивановна", GroupID: "g2", Course: 3},
	},
	Schedule: []ScheduleEntry{
		{
			ID:      "sch1",
			GroupID: "g1",
			Day:     "Понедельник",
			Lessons: []Lesson{
				{
					DisciplineID:   "d1",
					DisciplineName: "Алгоритмы и структуры данных",
					TeacherName:    "Оксана Ниловна Константинова",
					Type:           "Лекция",
					Room:           301,
					TimeSlot:       "9:00-10:30",
					WeekType:       "числитель",
				},
				{
					DisciplineID:   "d2",
					DisciplineName: "Базы данных",
					TeacherName:    "Оксана Ниловна Константинова",
					Type:           "Практика",
					Room:           205,
					TimeSlot:       "10:45-12:15",
					WeekType:       "знаменатель",
				},
			},
		},
		{
			ID:      "sch2",
			GroupID: "g1",
			Day:     "Вторник",
			Lessons: []Lesson{
				{
					DisciplineID:   "d3",
					DisciplineName: "Веб-технологии",
					TeacherName:    "Другой Преподаватель",
					Type:           "Лекция",
					Room:           310,
					TimeSlot:       "11:00-12:30",
					WeekType:       "каждую",
				},
			},
		},
	},
	Grades: []Grade{
		{ID: "gr1", StudentID: "s1", DisciplineID: "d1", Grade: "5", Date: "2026-04-10"},
		{ID: "gr2", StudentID: "s1", DisciplineID: "d2", Grade: "4", Date: "2026-06-15"},
		{ID: "gr3", StudentID: "s2", DisciplineID: "d3", Grade: "3", Date: "2026-04-20"},
	},
}
