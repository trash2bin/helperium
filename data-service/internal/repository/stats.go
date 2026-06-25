package repository

import (
	"context"
	"fmt"

	"github.com/agent-tutor/data-service/internal/db"
)

// Stats содержит количество записей во всех основных таблицах.
type Stats struct {
	Students    int `json:"students"`
	Teachers    int `json:"teachers"`
	Disciplines int `json:"disciplines"`
	Grades      int `json:"grades"`
	Schedule    int `json:"schedule"`
	Documents   int `json:"documents"`
}

// StatsRepo — доступ к статистике БД.
type StatsRepo struct {
	db     db.DB
	tables []string
}

func NewStatsRepo(database db.DB) *StatsRepo {
	return &StatsRepo{
		db:     database,
		tables: []string{"students", "teachers", "disciplines", "grades", "schedule", "documents"},
	}
}

// GetAll возвращает количество записей во всех таблицах.
func (r *StatsRepo) GetAll(ctx context.Context) (*Stats, error) {
	counts := make(map[string]int, len(r.tables))

	for _, table := range r.tables {
		var count int
		err := r.db.QueryRowContext(ctx,
			fmt.Sprintf("SELECT COUNT(*) FROM %s", table),
		).Scan(&count)
		if err != nil {
			return nil, fmt.Errorf("stats: table %s: %w", table, err)
		}
		counts[table] = count
	}

	return &Stats{
		Students:    counts["students"],
		Teachers:    counts["teachers"],
		Disciplines: counts["disciplines"],
		Grades:      counts["grades"],
		Schedule:    counts["schedule"],
		Documents:   counts["documents"],
	}, nil
}
