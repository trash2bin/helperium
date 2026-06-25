package repository

import (
	"context"
	"fmt"

	"github.com/agent-tutor/data-service/internal/db"
	"github.com/agent-tutor/data-service/internal/models"
)

// TeacherRepo — доступ к данным преподавателей и их расписания.
type TeacherRepo struct {
	db      db.DB
	groupFn func(ctx context.Context, groupID string) (*models.Group, error)
}

func NewTeacherRepo(database db.DB) *TeacherRepo {
	return &TeacherRepo{
		db:      database,
		groupFn: func(ctx context.Context, groupID string) (*models.Group, error) {
			return queryGroup(ctx, database, groupID)
		},
	}
}

// FindByName ищет преподавателя по полному ФИО.
func (r *TeacherRepo) FindByName(ctx context.Context, name string) (*models.Teacher, error) {
	row := r.db.QueryRowContext(ctx,
		`SELECT id, name, disciplines_json FROM teachers WHERE name = ?`, name,
	)
	var id, fullName, disciplinesJSON string
	err := row.Scan(&id, &fullName, &disciplinesJSON)
	if err != nil {
		return nil, fmt.Errorf("teacher find: %w", err)
	}

	return &models.Teacher{
		ID:          id,
		FullName:    fullName,
		Disciplines: parseStringArray(disciplinesJSON),
	}, nil
}

// GetSchedule возвращает расписание преподавателя, опционально по дню.
func (r *TeacherRepo) GetSchedule(ctx context.Context, teacherName string, day *string) ([]models.ScheduleEntry, error) {
	var query string
	var args []any

	if day != nil {
		query = `SELECT id, day, group_id, lessons_json FROM schedule WHERE day = ?`
		args = append(args, *day)
	} else {
		query = `SELECT id, day, group_id, lessons_json FROM schedule`
	}

	rows, err := r.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, fmt.Errorf("teacher schedule: %w", err)
	}
	defer rows.Close()

	var entries []models.ScheduleEntry
	for rows.Next() {
		var id, dayName, groupID, lessonsJSON string
		if err := rows.Scan(&id, &dayName, &groupID, &lessonsJSON); err != nil {
			continue
		}

		// Фильтруем уроки — оставляем только те, которые ведёт этот преподаватель
		allLessons := parseLessonsJSON(lessonsJSON)
		var teacherLessons []models.Lesson
		for _, l := range allLessons {
			if l.TeacherName == teacherName {
				teacherLessons = append(teacherLessons, l)
			}
		}

		if len(teacherLessons) > 0 {
			group, _ := r.groupFn(ctx, groupID)
			entries = append(entries, models.ScheduleEntry{
				ID:      id,
				Day:     dayName,
				Group:   group,
				Lessons: teacherLessons,
			})
		}
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("teacher schedule: %w", err)
	}

	return entries, nil
}

// ListAll возвращает всех преподавателей.
func (r *TeacherRepo) ListAll(ctx context.Context) ([]models.Teacher, error) {
	rows, err := r.db.QueryContext(ctx,
		`SELECT id, name, disciplines_json FROM teachers ORDER BY name`,
	)
	if err != nil {
		return nil, fmt.Errorf("teachers list: %w", err)
	}
	defer rows.Close()

	var teachers []models.Teacher
	for rows.Next() {
		var id, name, discJSON string
		if err := rows.Scan(&id, &name, &discJSON); err != nil {
			return nil, fmt.Errorf("teacher scan: %w", err)
		}
		teachers = append(teachers, models.Teacher{
			ID: id, FullName: name, Disciplines: parseStringArray(discJSON),
		})
	}
	return teachers, rows.Err()
}
