package repository

import (
	"context"
	"fmt"

	"github.com/agent-tutor/data-service/internal/db"
	"github.com/agent-tutor/data-service/internal/models"
)

// GradeRepo — доступ к данным об оценках.
type GradeRepo struct {
	db db.DB
}

func NewGradeRepo(database db.DB) *GradeRepo {
	return &GradeRepo{db: database}
}

// GetByStudent возвращает оценки студента, опционально отфильтрованные по дисциплине.
func (r *GradeRepo) GetByStudent(ctx context.Context, studentID string, disciplineID *string) ([]models.Grade, error) {
	var query string
	var args []any

	if disciplineID != nil {
		query = `
			SELECT g.id, g.student_id, g.discipline_id,
			       COALESCE(d.name, 'Неизвестная дисциплина') AS discipline_name,
			       g.grade, g.date
			FROM grades g
			LEFT JOIN disciplines d ON d.id = g.discipline_id
			WHERE g.student_id = ? AND g.discipline_id = ?
			ORDER BY g.date DESC, d.name ASC`
		args = []any{studentID, *disciplineID}
	} else {
		query = `
			SELECT g.id, g.student_id, g.discipline_id,
			       COALESCE(d.name, 'Неизвестная дисциплина') AS discipline_name,
			       g.grade, g.date
			FROM grades g
			LEFT JOIN disciplines d ON d.id = g.discipline_id
			WHERE g.student_id = ?
			ORDER BY g.date DESC, d.name ASC`
		args = []any{studentID}
	}

	rows, err := r.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, fmt.Errorf("grades: %w", err)
	}
	defer rows.Close()

	var grades []models.Grade
	for rows.Next() {
		var g models.Grade
		if err := rows.Scan(&g.ID, &g.StudentID, &g.DisciplineID, &g.DisciplineName, &g.Value, &g.Date); err != nil {
			return nil, fmt.Errorf("grade scan: %w", err)
		}
		grades = append(grades, g)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("grades: %w", err)
	}

	return grades, nil
}

// ListAll возвращает все оценки (для demo overview) с именем студента.
func (r *GradeRepo) ListAll(ctx context.Context) ([]models.Grade, error) {
	rows, err := r.db.QueryContext(ctx,
		`SELECT g.id, g.student_id, g.discipline_id,
		        COALESCE(s.name, 'Неизвестный студент') AS student_name,
		        COALESCE(d.name, 'Неизвестная дисциплина') AS discipline_name,
		        g.grade, g.date
		 FROM grades g
		 LEFT JOIN students s ON s.id = g.student_id
		 LEFT JOIN disciplines d ON d.id = g.discipline_id
		 ORDER BY g.date DESC
		 LIMIT 80`,
	)
	if err != nil {
		return nil, fmt.Errorf("grades list: %w", err)
	}
	defer rows.Close()

	var grades []models.Grade
	for rows.Next() {
		var g models.Grade
		if err := rows.Scan(&g.ID, &g.StudentID, &g.DisciplineID, &g.StudentName, &g.DisciplineName, &g.Value, &g.Date); err != nil {
			return nil, fmt.Errorf("grade scan: %w", err)
		}
		grades = append(grades, g)
	}
	return grades, rows.Err()
}
