// Package seedgen загружает и применяет seed-данные к БД университета.
//
// Используется ТОЛЬКО в dev-режиме через CLI-флаг --seed в data-service.
// Если БД уже содержит данные — паникует (предотвращает перезапись реальной prod-БД).
package seedgen

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"os"

	"github.com/agent-tutor/data-service/internal/db"
)

// Seed — корневая структура файла fixtures/seed.json.
type Seed struct {
	Groups      []Group         `json:"groups"`
	Students    []Student       `json:"students"`
	Teachers    []Teacher       `json:"teachers"`
	Disciplines []Discipline    `json:"disciplines"`
	Schedule    []ScheduleEntry `json:"schedule"`
	Grades      []Grade         `json:"grades"`
}

type Group struct {
	ID         string `json:"id"`
	Name       string `json:"name"`
	Speciality string `json:"speciality"`
}

type Student struct {
	ID      string `json:"id"`
	Name    string `json:"name"`
	GroupID string `json:"group_id"`
	Course  int    `json:"course"`
}

type Teacher struct {
	ID          string   `json:"id"`
	Name        string   `json:"name"`
	Disciplines []string `json:"disciplines"`
}

type Discipline struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	Description string `json:"description"`
}

type ScheduleEntry struct {
	ID      string   `json:"id"`
	GroupID string   `json:"group_id"`
	Day     string   `json:"day"`
	Lessons []Lesson `json:"lessons"`
}

type Lesson struct {
	DisciplineID   string `json:"discipline_id"`
	DisciplineName string `json:"discipline_name"`
	TeacherName    string `json:"teacher_name"`
	Type           string `json:"type"`
	Room           int    `json:"room"`
	TimeSlot       string `json:"time_slot"`
	WeekType       string `json:"week_type"`
}

type Grade struct {
	ID           string `json:"id"`
	StudentID    string `json:"student_id"`
	DisciplineID string `json:"discipline_id"`
	Grade        string `json:"grade"`
	Date         string `json:"date"`
}

// Load читает и парсит JSON-файл с seed-данными.
func Load(path string) (*Seed, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read seed file %q: %w", path, err)
	}
	var s Seed
	if err := json.Unmarshal(data, &s); err != nil {
		return nil, fmt.Errorf("parse seed file %q: %w", path, err)
	}
	return &s, nil
}

// ErrDatabaseNotEmpty — БД уже содержит записи, seed отменён.
var ErrDatabaseNotEmpty = errors.New("database already contains data, seed aborted")

// ErrSeedFileMissing — файл с seed-данными не найден.
var ErrSeedFileMissing = errors.New("seed file not found")

// Apply применяет данные к БД в правильном порядке FK:
// groups → disciplines → teachers → students → schedule → grades.
//
// Если БД не пустая — возвращает ErrDatabaseNotEmpty.
func Apply(ctx context.Context, database db.DB, seed *Seed) error {
	var tableName string
	row := database.QueryRowContext(ctx,
		"SELECT name FROM sqlite_master WHERE type='table' AND name='groups' LIMIT 1")
	if err := row.Scan(&tableName); err != nil {
		if err := applySchema(ctx, database); err != nil {
			return fmt.Errorf("apply schema: %w", err)
		}
	}

	var count int
	row = database.QueryRowContext(ctx, "SELECT COUNT(*) FROM groups")
	if err := row.Scan(&count); err != nil {
		return fmt.Errorf("check groups empty: %w", err)
	}
	if count > 0 {
		return fmt.Errorf("%w: groups has %d rows", ErrDatabaseNotEmpty, count)
	}

	if err := insertGroups(ctx, database, seed.Groups); err != nil {
		return fmt.Errorf("insert groups: %w", err)
	}
	if err := insertDisciplines(ctx, database, seed.Disciplines); err != nil {
		return fmt.Errorf("insert disciplines: %w", err)
	}
	if err := insertTeachers(ctx, database, seed.Teachers); err != nil {
		return fmt.Errorf("insert teachers: %w", err)
	}
	if err := insertStudents(ctx, database, seed.Students); err != nil {
		return fmt.Errorf("insert students: %w", err)
	}
	if err := insertSchedule(ctx, database, seed.Schedule); err != nil {
		return fmt.Errorf("insert schedule: %w", err)
	}
	if err := insertGrades(ctx, database, seed.Grades); err != nil {
		return fmt.Errorf("insert grades: %w", err)
	}

	slog.Info("seed applied",
		"groups", len(seed.Groups),
		"students", len(seed.Students),
		"teachers", len(seed.Teachers),
		"disciplines", len(seed.Disciplines),
		"schedule", len(seed.Schedule),
		"grades", len(seed.Grades),
	)
	return nil
}

func applySchema(ctx context.Context, database db.DB) error {
	if db.SchemaSQL == "" {
		return fmt.Errorf("embedded schema SQL is empty")
	}
	if _, err := database.ExecContext(ctx, db.SchemaSQL); err != nil {
		return err
	}
	slog.Info("schema applied from embedded SQL")
	return nil
}

func insertGroups(ctx context.Context, database db.DB, groups []Group) error {
	for _, g := range groups {
		_, err := database.ExecContext(ctx,
			"INSERT INTO groups (id, name, speciality) VALUES (?, ?, ?)",
			g.ID, g.Name, g.Speciality)
		if err != nil {
			return fmt.Errorf("group %q: %w", g.ID, err)
		}
	}
	return nil
}

func insertDisciplines(ctx context.Context, database db.DB, disciplines []Discipline) error {
	for _, d := range disciplines {
		_, err := database.ExecContext(ctx,
			"INSERT INTO disciplines (id, name, description) VALUES (?, ?, ?)",
			d.ID, d.Name, d.Description)
		if err != nil {
			return fmt.Errorf("discipline %q: %w", d.ID, err)
		}
	}
	return nil
}

func insertTeachers(ctx context.Context, database db.DB, teachers []Teacher) error {
	for _, t := range teachers {
		discJSON, err := json.Marshal(t.Disciplines)
		if err != nil {
			return fmt.Errorf("marshal teacher %q disciplines: %w", t.ID, err)
		}
		_, err = database.ExecContext(ctx,
			"INSERT INTO teachers (id, name, disciplines_json) VALUES (?, ?, ?)",
			t.ID, t.Name, string(discJSON))
		if err != nil {
			return fmt.Errorf("teacher %q: %w", t.ID, err)
		}
	}
	return nil
}

func insertStudents(ctx context.Context, database db.DB, students []Student) error {
	for _, s := range students {
		_, err := database.ExecContext(ctx,
			"INSERT INTO students (id, name, group_id, course) VALUES (?, ?, ?, ?)",
			s.ID, s.Name, s.GroupID, s.Course)
		if err != nil {
			return fmt.Errorf("student %q: %w", s.ID, err)
		}
	}
	return nil
}

func insertSchedule(ctx context.Context, database db.DB, schedule []ScheduleEntry) error {
	for _, e := range schedule {
		lessonsJSON, err := json.Marshal(e.Lessons)
		if err != nil {
			return fmt.Errorf("marshal schedule %q lessons: %w", e.ID, err)
		}
		_, err = database.ExecContext(ctx,
			"INSERT INTO schedule (id, day, group_id, lessons_json) VALUES (?, ?, ?, ?)",
			e.ID, e.Day, e.GroupID, string(lessonsJSON))
		if err != nil {
			return fmt.Errorf("schedule %q: %w", e.ID, err)
		}
	}
	return nil
}

func insertGrades(ctx context.Context, database db.DB, grades []Grade) error {
	for _, g := range grades {
		_, err := database.ExecContext(ctx,
			"INSERT INTO grades (id, student_id, discipline_id, grade, date) VALUES (?, ?, ?, ?, ?)",
			g.ID, g.StudentID, g.DisciplineID, g.Grade, g.Date)
		if err != nil {
			return fmt.Errorf("grade %q: %w", g.ID, err)
		}
	}
	return nil
}
