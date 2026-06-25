package repository

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"

	"github.com/agent-tutor/data-service/internal/db"
	"github.com/agent-tutor/data-service/internal/models"
)

// ── Helpers для парсинга JSON-полей из текущей схемы (lessons_json, disciplines_json) ──

// rawLesson — промежуточная структура для десериализации lesson из JSON.
type rawLesson struct {
	DisciplineID   string `json:"discipline_id"`
	DisciplineName string `json:"discipline_name"`
	TeacherName    string `json:"teacher_name"`
	Room           int    `json:"room"`
}

// parseLessonsJSON парсит lessons_json в []Lesson.
func parseLessonsJSON(jsonStr string) []models.Lesson {
	var raw []rawLesson
	if err := json.Unmarshal([]byte(jsonStr), &raw); err != nil {
		return nil
	}
	lessons := make([]models.Lesson, len(raw))
	for i, r := range raw {
		lessons[i] = models.Lesson{
			DisciplineID:   r.DisciplineID,
			DisciplineName: r.DisciplineName,
			TeacherName:    r.TeacherName,
			Room:           r.Room,
		}
	}
	return lessons
}

// parseStringArray парсит JSON-массив строк.
func parseStringArray(jsonStr string) []string {
	var arr []string
	if err := json.Unmarshal([]byte(jsonStr), &arr); err != nil {
		return nil
	}
	return arr
}

// extractDisciplineIDs извлекает уникальные discipline_id из lessons_json.
func extractDisciplineIDs(jsonStr string) []string {
	lessons := parseLessonsJSON(jsonStr)
	seen := make(map[string]struct{})
	var ids []string
	for _, l := range lessons {
		if _, ok := seen[l.DisciplineID]; !ok && l.DisciplineID != "" {
			seen[l.DisciplineID] = struct{}{}
			ids = append(ids, l.DisciplineID)
		}
	}
	return ids
}

// strOrEmpty возвращает строку или "", если указатель nil.
func strOrEmpty(s *string) string {
	if s == nil {
		return ""
	}
	return *s
}

// scanScheduleEntries сканирует строки расписания (JOIN schedule + groups).
func scanScheduleEntries(rows *sql.Rows) ([]models.ScheduleEntry, error) {
	var entries []models.ScheduleEntry
	for rows.Next() {
		var (
			id          string
			day         string
			groupID     string
			groupName   string
			speciality  string
			lessonsJSON string
		)
		if err := rows.Scan(&id, &day, &groupID, &groupName, &speciality, &lessonsJSON); err != nil {
			return nil, fmt.Errorf("schedule scan: %w", err)
		}

		entries = append(entries, models.ScheduleEntry{
			ID: id,
			Group: &models.Group{
				ID:         groupID,
				Name:       groupName,
				Speciality: speciality,
			},
			Day:     day,
			Lessons: parseLessonsJSON(lessonsJSON),
		})
	}
	return entries, rows.Err()
}

// ListAllSchedule возвращает всё расписание (для demo overview).
func ListAllSchedule(ctx context.Context, database db.DB) ([]models.ScheduleEntry, error) {
	rows, err := database.QueryContext(ctx,
		`SELECT s.id, s.day, s.group_id, g.name, g.speciality, s.lessons_json
		 FROM schedule s LEFT JOIN groups g ON g.id = s.group_id
		 ORDER BY g.name, s.day`,
	)
	if err != nil {
		return nil, fmt.Errorf("schedule list: %w", err)
	}
	defer rows.Close()
	return scanScheduleEntries(rows)
}
