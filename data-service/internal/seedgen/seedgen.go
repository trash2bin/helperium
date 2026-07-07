// Package seedgen loads and applies seed data to a database.
//
// Used ONLY in dev-mode through CLI flag --seed in data-service.
// If DB already contains data — refuses (prevents overwrite).
//
// In phase 3.3 moved to /cmd/seed-cli for dev/demo,
// not part of data-service prod code.
package seedgen

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"strings"
)

// ExecContext — minimal interface for seed operations.
type ExecContext interface {
	ExecContext(ctx context.Context, query string, args ...any) (sql.Result, error)
	QueryRowContext(ctx context.Context, query string, args ...any) *sql.Row
}

// schemaDDL — DDL for university schema.
// Used only in seed mode (dev-only).
const schemaDDL = `
CREATE TABLE IF NOT EXISTS groups (
    id TEXT PRIMARY KEY,
    name TEXT,
    speciality TEXT
);
CREATE TABLE IF NOT EXISTS students (
    id TEXT PRIMARY KEY,
    name TEXT,
    group_id TEXT,
    course INTEGER,
    FOREIGN KEY (group_id) REFERENCES groups (id)
);
CREATE TABLE IF NOT EXISTS teachers (
    id TEXT PRIMARY KEY,
    name TEXT,
    disciplines_json TEXT
);
CREATE TABLE IF NOT EXISTS disciplines (
    id TEXT PRIMARY KEY,
    name TEXT,
    description TEXT
);
CREATE TABLE IF NOT EXISTS grades (
    id TEXT PRIMARY KEY,
    student_id TEXT,
    discipline_id TEXT,
    grade TEXT,
    date TEXT,
    FOREIGN KEY (student_id) REFERENCES students (id),
    FOREIGN KEY (discipline_id) REFERENCES disciplines (id)
);
CREATE TABLE IF NOT EXISTS schedule (
    id TEXT PRIMARY KEY,
    day TEXT,
    group_id TEXT,
    lessons_json TEXT,
    FOREIGN KEY (group_id) REFERENCES groups (id)
);
`

// PlaceholderFunc generates a placeholder for a given driver:
// "?" for SQLite, "$1" for PostgreSQL (1-indexed).
type PlaceholderFunc func(index int) string

// SQLitePlaceholder returns "?" regardless of index.
func SQLitePlaceholder(_ int) string { return "?" }

// PostgresPlaceholder returns "$1", "$2", etc.
func PostgresPlaceholder(index int) string { return fmt.Sprintf("$%d", index) }

// Seed — root structure of fixtures/seed.json.
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

// Load reads and parses a seed JSON file.
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

// ErrDatabaseNotEmpty — DB already contains records, seed aborted.
var ErrDatabaseNotEmpty = errors.New("database already contains data, seed aborted")

// ErrSeedFileMissing — seed file not found.
var ErrSeedFileMissing = errors.New("seed file not found")

// Apply applies data to DB in FK order:
// groups → disciplines → teachers → students → schedule → grades.
//
// Uses embedded schemaDDL and SQLite placeholders.
// Returns ErrDatabaseNotEmpty if DB already contains university data.
// For tests/scenarios where DDL is generated from config — use ApplyWithDDL.
func Apply(ctx context.Context, database ExecContext, seed *Seed) error {
	// Guard: refuse to seed a non-empty DB (seed-cli safety).
	// The query uses 'groups' — the first table seeded in FK order.
	// If the table doesn't exist, Scan returns an error → let ApplyWithDDL create it.
	var count int
	if err := database.QueryRowContext(ctx, "SELECT COUNT(*) FROM groups").Scan(&count); err == nil && count > 0 {
		return fmt.Errorf("%w: groups has %d rows", ErrDatabaseNotEmpty, count)
	}
	return ApplyWithDDL(ctx, database, schemaDDL, seed, SQLitePlaceholder, "sqlite")
}

// ApplyWithDDL applies DDL and seed data to DB.
// DDL is split by ';' and each statement is executed individually
// (SQLite supports multi-statement, PostgreSQL does not — splitting keeps both paths safe).
// Seed is inserted in FK order: groups → disciplines → teachers → students → schedule → grades.
// phFn — placeholder generator (SQLitePlaceholder or PostgresPlaceholder).
// driver — "sqlite" or "postgres". Controls idempotent INSERT syntax:
//
//	Postgres: INSERT INTO ... VALUES (...) ON CONFLICT (id) DO NOTHING
//	SQLite:   INSERT OR IGNORE INTO ... VALUES (...)
//
// Unlike Apply, does NOT check DB emptiness — caller controls when DDL is applied.
func ApplyWithDDL(ctx context.Context, database ExecContext, ddl string, seed *Seed, phFn PlaceholderFunc, driver string) error {
	if ddl != "" {
		for _, stmt := range splitDDL(ddl) {
			if stmt == "" {
				continue
			}
			if _, err := database.ExecContext(ctx, stmt); err != nil {
				return fmt.Errorf("apply DDL: %w", err)
			}
		}
		slog.Info("DDL applied from parameter")
	}

	if err := insertGroups(ctx, database, seed.Groups, phFn, driver); err != nil {
		return fmt.Errorf("insert groups: %w", err)
	}
	if err := insertDisciplines(ctx, database, seed.Disciplines, phFn, driver); err != nil {
		return fmt.Errorf("insert disciplines: %w", err)
	}
	if err := insertTeachers(ctx, database, seed.Teachers, phFn, driver); err != nil {
		return fmt.Errorf("insert teachers: %w", err)
	}
	if err := insertStudents(ctx, database, seed.Students, phFn, driver); err != nil {
		return fmt.Errorf("insert students: %w", err)
	}
	if err := insertSchedule(ctx, database, seed.Schedule, phFn, driver); err != nil {
		return fmt.Errorf("insert schedule: %w", err)
	}
	if err := insertGrades(ctx, database, seed.Grades, phFn, driver); err != nil {
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

// buildInsert builds an idempotent INSERT statement for the given driver and column list.
//
// Postgres: INSERT INTO tbl (c1, c2, ...) VALUES ($1, $2, ...) ON CONFLICT (id) DO NOTHING
// SQLite:   INSERT OR IGNORE INTO tbl (c1, c2, ...) VALUES (?, ?, ...)
func buildInsert(driver, table string, cols []string, phFn PlaceholderFunc) string {
	var quoted []string
	for _, c := range cols {
		quoted = append(quoted, `"`+c+`"`)
	}
	var phs []string
	for i := 1; i <= len(cols); i++ {
		phs = append(phs, phFn(i))
	}
	values := strings.Join(phs, ", ")

	prefix := "INSERT"
	suffix := ""
	switch driver {
	case "sqlite":
		prefix = "INSERT OR IGNORE"
	case "postgres":
		suffix = " ON CONFLICT (\"id\") DO NOTHING"
	}
	return fmt.Sprintf("%s INTO %s (%s) VALUES (%s)%s",
		prefix, `"`+table+`"`, strings.Join(quoted, ", "), values, suffix,
	)
}

func insertGroups(ctx context.Context, db ExecContext, groups []Group, ph PlaceholderFunc, driver string) error {
	query := buildInsert(driver, "groups", []string{"id", "name", "speciality"}, ph)
	for _, g := range groups {
		_, err := db.ExecContext(ctx, query, g.ID, g.Name, g.Speciality)
		if err != nil {
			return fmt.Errorf("group %q: %w", g.ID, err)
		}
	}
	return nil
}

func insertDisciplines(ctx context.Context, db ExecContext, disciplines []Discipline, ph PlaceholderFunc, driver string) error {
	query := buildInsert(driver, "disciplines", []string{"id", "name", "description"}, ph)
	for _, d := range disciplines {
		_, err := db.ExecContext(ctx, query, d.ID, d.Name, d.Description)
		if err != nil {
			return fmt.Errorf("discipline %q: %w", d.ID, err)
		}
	}
	return nil
}

func insertTeachers(ctx context.Context, db ExecContext, teachers []Teacher, ph PlaceholderFunc, driver string) error {
	query := buildInsert(driver, "teachers", []string{"id", "name", "disciplines_json"}, ph)
	for _, t := range teachers {
		discJSON, err := json.Marshal(t.Disciplines)
		if err != nil {
			return fmt.Errorf("marshal teacher %q disciplines: %w", t.ID, err)
		}
		_, err = db.ExecContext(ctx, query, t.ID, t.Name, string(discJSON))
		if err != nil {
			return fmt.Errorf("teacher %q: %w", t.ID, err)
		}
	}
	return nil
}

func insertStudents(ctx context.Context, db ExecContext, students []Student, ph PlaceholderFunc, driver string) error {
	query := buildInsert(driver, "students", []string{"id", "name", "group_id", "course"}, ph)
	for _, s := range students {
		_, err := db.ExecContext(ctx, query, s.ID, s.Name, s.GroupID, s.Course)
		if err != nil {
			return fmt.Errorf("student %q: %w", s.ID, err)
		}
	}
	return nil
}

func insertSchedule(ctx context.Context, db ExecContext, schedule []ScheduleEntry, ph PlaceholderFunc, driver string) error {
	query := buildInsert(driver, "schedule", []string{"id", "day", "group_id", "lessons_json"}, ph)
	for _, e := range schedule {
		lessonsJSON, err := json.Marshal(e.Lessons)
		if err != nil {
			return fmt.Errorf("marshal schedule %q lessons: %w", e.ID, err)
		}
		_, err = db.ExecContext(ctx, query, e.ID, e.Day, e.GroupID, string(lessonsJSON))
		if err != nil {
			return fmt.Errorf("schedule %q: %w", e.ID, err)
		}
	}
	return nil
}

func insertGrades(ctx context.Context, db ExecContext, grades []Grade, ph PlaceholderFunc, driver string) error {
	query := buildInsert(driver, "grades", []string{"id", "student_id", "discipline_id", "grade", "date"}, ph)
	for _, g := range grades {
		_, err := db.ExecContext(ctx, query, g.ID, g.StudentID, g.DisciplineID, g.Grade, g.Date)
		if err != nil {
			return fmt.Errorf("grade %q: %w", g.ID, err)
		}
	}
	return nil
}

// splitDDL splits a multi-statement DDL string into individual SQL statements.
// Statements are separated by ';' — empty strings are skipped.
//
// LIMITATION: naive split — a ';' inside a SQL string literal or identifier
// would cause incorrect split. This is safe today because ALL DDL passed to
// this function is generated (GenerateDDL or embedded schemaDDL), never user-
// supplied, and none of these produce ';' outside statement terminators.
func splitDDL(ddl string) []string {
	var out []string
	cur := ""
	for _, r := range ddl {
		if r == ';' {
			trimmed := strings.TrimSpace(cur)
			if trimmed != "" {
				out = append(out, trimmed)
			}
			cur = ""
			continue
		}
		cur += string(r)
	}
	trimmed := strings.TrimSpace(cur)
	if trimmed != "" {
		out = append(out, trimmed)
	}
	return out
}
