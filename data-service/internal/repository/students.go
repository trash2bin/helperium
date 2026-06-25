// Package repository содержит слой доступа к данным.
// Это ЕДИНСТВЕННЫЙ пакет, который знает имена таблиц и колонок.
// При смене схемы БД переписываются только эти файлы.
package repository

import (
	"context"
	"database/sql"
	"fmt"

	"github.com/agent-tutor/data-service/internal/db"
	"github.com/agent-tutor/data-service/internal/models"
)

// StudentRepo — доступ к данным студентов и их расписания.
type StudentRepo struct {
	db db.DB
}

func NewStudentRepo(database db.DB) *StudentRepo {
	return &StudentRepo{db: database}
}

// GetByID возвращает карточку студента по ID.
func (r *StudentRepo) GetByID(ctx context.Context, id string) (*models.Student, error) {
	row := r.db.QueryRowContext(ctx,
		`SELECT s.id, s.name, s.course,
		        g.id, g.name, g.speciality
		 FROM students s
		 LEFT JOIN groups g ON g.id = s.group_id
		 WHERE s.id = ?`, id,
	)
	return scanStudent(row)
}

// FindByName ищет студента по полному ФИО.
func (r *StudentRepo) FindByName(ctx context.Context, name string) (*models.Student, error) {
	row := r.db.QueryRowContext(ctx,
		`SELECT s.id, s.name, s.course,
		        g.id, g.name, g.speciality
		 FROM students s
		 LEFT JOIN groups g ON g.id = s.group_id
		 WHERE s.name = ?`, name,
	)
	return scanStudent(row)
}

// GetDisciplines возвращает дисциплины студента (через группу → расписание).
func (r *StudentRepo) GetDisciplines(ctx context.Context, studentID string) ([]models.Discipline, error) {
	// Получаем group_id студента
	var groupID string
	err := r.db.QueryRowContext(ctx,
		`SELECT group_id FROM students WHERE id = ?`, studentID,
	).Scan(&groupID)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("student disciplines: %w", err)
	}

	// Получаем все lessons_json из расписания группы
	rows, err := r.db.QueryContext(ctx,
		`SELECT lessons_json FROM schedule WHERE group_id = ?`, groupID,
	)
	if err != nil {
		return nil, fmt.Errorf("student disciplines: %w", err)
	}
	defer rows.Close()

	// Собираем уникальные discipline_id из JSON-полей
	disciplineIDs := make(map[string]struct{})
	for rows.Next() {
		var jsonStr string
		if err := rows.Scan(&jsonStr); err != nil {
			continue
		}
		for _, id := range extractDisciplineIDs(jsonStr) {
			disciplineIDs[id] = struct{}{}
		}
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("student disciplines: %w", err)
	}

	if len(disciplineIDs) == 0 {
		return nil, nil
	}

	return queryDisciplinesByIDs(ctx, r.db, disciplineIDs)
}

// GetSchedule возвращает расписание группы, опционально отфильтрованное по дню.
func (r *StudentRepo) GetSchedule(ctx context.Context, groupID string, day *string) ([]models.ScheduleEntry, error) {
	var rows *sql.Rows
	var err error

	if day != nil {
		rows, err = r.db.QueryContext(ctx,
			`SELECT s.id, s.day, s.group_id, g.name, g.speciality, s.lessons_json
			 FROM schedule s
			 LEFT JOIN groups g ON g.id = s.group_id
			 WHERE s.group_id = ? AND s.day = ?`, groupID, *day,
		)
	} else {
		rows, err = r.db.QueryContext(ctx,
			`SELECT s.id, s.day, s.group_id, g.name, g.speciality, s.lessons_json
			 FROM schedule s
			 LEFT JOIN groups g ON g.id = s.group_id
			 WHERE s.group_id = ?`, groupID,
		)
	}
	if err != nil {
		return nil, fmt.Errorf("schedule: %w", err)
	}
	defer rows.Close()

	return scanScheduleEntries(rows)
}

// scanStudent сканирует строку студента (JOIN students + groups).
func scanStudent(row *sql.Row) (*models.Student, error) {
	var (
		id           string
		name         string
		course       *int
		groupID      *string
		groupName    *string
		speciality   *string
	)
	err := row.Scan(&id, &name, &course, &groupID, &groupName, &speciality)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("student scan: %w", err)
	}

	s := &models.Student{
		ID:       id,
		FullName: name,
		Course:   course,
	}

	if groupID != nil {
		s.Group = &models.Group{
			ID:         *groupID,
			Name:       strOrEmpty(groupName),
			Speciality: strOrEmpty(speciality),
		}
	}
	return s, nil
}

// ListAll возвращает всех студентов с группами.
func (r *StudentRepo) ListAll(ctx context.Context) ([]models.Student, error) {
	rows, err := r.db.QueryContext(ctx,
		`SELECT s.id, s.name, s.course, g.id, g.name, g.speciality
		 FROM students s LEFT JOIN groups g ON g.id = s.group_id
		 ORDER BY g.name, s.name`,
	)
	if err != nil {
		return nil, fmt.Errorf("students list: %w", err)
	}
	defer rows.Close()

	var students []models.Student
	for rows.Next() {
		var (
			id, name string
			course     *int
			gID, gName, gSpec *string
		)
		if err := rows.Scan(&id, &name, &course, &gID, &gName, &gSpec); err != nil {
			return nil, fmt.Errorf("student scan: %w", err)
		}
		s := models.Student{ID: id, FullName: name, Course: course}
		if gID != nil {
			s.Group = &models.Group{ID: *gID, Name: strOrEmpty(gName), Speciality: strOrEmpty(gSpec)}
		}
		students = append(students, s)
	}
	return students, rows.Err()
}
