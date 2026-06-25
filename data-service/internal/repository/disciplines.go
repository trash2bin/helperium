package repository

import (
	"context"
	"fmt"

	"github.com/agent-tutor/data-service/internal/db"
	"github.com/agent-tutor/data-service/internal/models"
)

// DisciplineRepo — доступ к данным о дисциплинах.
type DisciplineRepo struct {
	db db.DB
}

func NewDisciplineRepo(database db.DB) *DisciplineRepo {
	return &DisciplineRepo{db: database}
}

// GetAll возвращает список всех дисциплин.
func (r *DisciplineRepo) GetAll(ctx context.Context) ([]models.Discipline, error) {
	rows, err := r.db.QueryContext(ctx,
		`SELECT id, name, description FROM disciplines ORDER BY name ASC`,
	)
	if err != nil {
		return nil, fmt.Errorf("disciplines: %w", err)
	}
	defer rows.Close()

	var disciplines []models.Discipline
	for rows.Next() {
		var d models.Discipline
		if err := rows.Scan(&d.ID, &d.Name, &d.Description); err != nil {
			return nil, fmt.Errorf("discipline scan: %w", err)
		}
		disciplines = append(disciplines, d)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("disciplines: %w", err)
	}

	return disciplines, nil
}

// queryDisciplinesByIDs возвращает дисциплины по набору ID.
func queryDisciplinesByIDs(ctx context.Context, database db.DB, ids map[string]struct{}) ([]models.Discipline, error) {
	if len(ids) == 0 {
		return nil, nil
	}

	// Строим IN (?, ?, ?)
	placeholders := ""
	idSlice := make([]any, 0, len(ids))
	i := 0
	for id := range ids {
		if i > 0 {
			placeholders += ", "
		}
		placeholders += "?"
		idSlice = append(idSlice, id)
		i++
	}

	query := fmt.Sprintf(
		`SELECT id, name, description FROM disciplines WHERE id IN (%s) ORDER BY name ASC`,
		placeholders,
	)

	rows, err := database.QueryContext(ctx, query, idSlice...)
	if err != nil {
		return nil, fmt.Errorf("disciplines by ids: %w", err)
	}
	defer rows.Close()

	var disciplines []models.Discipline
	for rows.Next() {
		var d models.Discipline
		if err := rows.Scan(&d.ID, &d.Name, &d.Description); err != nil {
			return nil, fmt.Errorf("discipline scan: %w", err)
		}
		disciplines = append(disciplines, d)
	}
	return disciplines, rows.Err()
}

// queryGroup возвращает группу по ID.
func queryGroup(ctx context.Context, database db.DB, groupID string) (*models.Group, error) {
	row := database.QueryRowContext(ctx,
		`SELECT id, name, speciality FROM groups WHERE id = ?`, groupID,
	)
	var g models.Group
	err := row.Scan(&g.ID, &g.Name, &g.Speciality)
	if err != nil {
		return nil, err
	}
	return &g, nil
}
