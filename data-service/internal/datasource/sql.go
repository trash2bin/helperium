package datasource

import (
	"context"
	"database/sql"
	"fmt"
	"log"
	"math"
	"time"

	"github.com/trash2bin/helperium/data-service/internal/query"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// Querier — минимальный интерфейс для выполнения SELECT-запросов.
// Используется SQLDataSource вместо ReadOnlyDB, чтобы можно было
// подставить runtime.AdapterSubset из endpoint_builder'а.
type Querier interface {
	QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error)
}

// SQLDataSource — DataSource для SQL-баз (SQLite/Postgres).
//
// Использует query.Engine для построения SQL и Querier для safe execution.
// Field-имена проходят через whitelist на основе entity.Fields.
// TenantID встраивается на уровне Query, а не параметров LLM.
type SQLDataSource struct {
	db        Querier
	engine    *query.Engine
	adapter   query.AdapterSubset
	entityMap map[string]config.Entity
	timeout   time.Duration
}

// NewSQLDataSource создаёт SQLDataSource.
//
// Параметры:
//   - db: read-only пул соединений или любой Querier
//   - adapter: адаптер для квотирования и placeholder'ов
//   - entities: список сущностей из конфига (для whitelist полей)
//   - timeout: per-query timeout (0 = без таймаута)
func NewSQLDataSource(db Querier, adapter query.AdapterSubset, entities []config.Entity, timeout time.Duration) *SQLDataSource {
	entityMap := make(map[string]config.Entity, len(entities))
	for i := range entities {
		entityMap[entities[i].Name] = entities[i]
	}
	return &SQLDataSource{
		db:        db,
		engine:    query.NewEngine(adapter),
		adapter:   adapter,
		entityMap: entityMap,
		timeout:   timeout,
	}
}

func (s *SQLDataSource) Type() string { return "sql" }

func (s *SQLDataSource) Close() error {
	if c, ok := s.db.(interface{ Close() error }); ok {
		return c.Close()
	}
	return nil
}

// ── DataSource interface stubs ─────────────────────────────────────────────
// These methods satisfy the DataSource interface but are never called.
// The DataSourceHandler only invokes Schema().
//
// Search / Filter / GetByID / Count / Distinct have full implementations
// in the search.Strategy pipeline (GrepStrategy, FilterStrategy, etc.)
// and their corresponding handlers.

func (s *SQLDataSource) Search(_ context.Context, _ *Query) (*Result, error) {
	return nil, fmt.Errorf("Search not used on SQLDataSource — use GrepStrategy instead")
}

func (s *SQLDataSource) Filter(_ context.Context, _ *Query) (*Result, error) {
	return nil, fmt.Errorf("Filter not used on SQLDataSource — use FilterStrategy instead")
}

func (s *SQLDataSource) GetByID(_ context.Context, _ string, _ any) (*Record, error) {
	return nil, fmt.Errorf("GetByID not used on SQLDataSource — use GetByIDHandler instead")
}

func (s *SQLDataSource) Count(_ context.Context, _ *Query) (int64, error) {
	return 0, fmt.Errorf("Count not used on SQLDataSource — use CountHandler instead")
}

func (s *SQLDataSource) Distinct(_ context.Context, _, _ string) ([]string, error) {
	return nil, fmt.Errorf("Distinct not used on SQLDataSource — use DistinctHandler instead")
}

// ── entity whitelist ───────────────────────────────────────────────────────

func (s *SQLDataSource) entity(name string) (config.Entity, error) {
	e, ok := s.entityMap[name]
	if !ok {
		return e, fmt.Errorf("entity %q not found", name)
	}
	return e, nil
}

// ── timeout helper ──────────────────────────────────────────────────────────

func (s *SQLDataSource) withTimeout(ctx context.Context) (context.Context, context.CancelFunc) {
	if s.timeout > 0 {
		return context.WithTimeout(ctx, s.timeout)
	}
	return ctx, func() {}
}

// ===========================================================================
// Schema — meta-information about an entity (total, distinct, min/max/avg).
// Used by LLM for discovery before search.
// ===========================================================================

func (s *SQLDataSource) Schema(ctx context.Context, entityName string) (*SchemaInfo, error) {
	start := time.Now()
	ctx, cancel := s.withTimeout(ctx)
	defer cancel()

	entity, err := s.entity(entityName)
	if err != nil {
		return nil, err
	}

	a := s.adapter
	info := &SchemaInfo{
		Entity: entityName,
		Fields: make(map[string]FieldMeta),
	}

	// 1. Total count
	totalSQL := fmt.Sprintf("SELECT COUNT(*) FROM %s", a.QuoteIdentifier(entity.Table))
	totalRows, err := s.db.QueryContext(ctx, totalSQL)
	if err != nil {
		log.Printf("DB error in Schema count: %v", err)
	} else if totalRows.Next() {
		_ = totalRows.Scan(&info.Total)
		_ = totalRows.Close()
	}

	// 2. Per-field metadata
	for _, f := range entity.Fields {
		if f.PrimaryKey != nil && *f.PrimaryKey {
			continue
		}
		if f.Column == "tenant_id" {
			continue
		}

		qName := a.QuoteIdentifier(f.Column)
		meta := FieldMeta{Type: string(f.Type)}

		switch f.Type {
		case config.FieldTypeString:
			// Distinct values (top 15)
			sqlStr := fmt.Sprintf("SELECT DISTINCT %s FROM %s WHERE %s IS NOT NULL AND %s != '' ORDER BY %s LIMIT 15",
				qName, a.QuoteIdentifier(entity.Table), qName, qName, qName)
			rows, err := s.db.QueryContext(ctx, sqlStr)
			if err == nil {
				var vals []string
				for rows.Next() {
					var v string
					_ = rows.Scan(&v)
					vals = append(vals, v)
				}
				_ = rows.Close()
				meta.Distinct = vals
			}

		case config.FieldTypeInt, config.FieldTypeFloat:
			// Min, Max, Avg
			sqlStr := fmt.Sprintf("SELECT MIN(%s), MAX(%s), AVG(%s) FROM %s",
				qName, qName, qName, a.QuoteIdentifier(entity.Table))
			statRows, err := s.db.QueryContext(ctx, sqlStr)
			if err == nil && statRows.Next() {
				var min, max, avg sql.NullFloat64
				_ = statRows.Scan(&min, &max, &avg)
				_ = statRows.Close()
				if min.Valid {
					meta.Min = f64ptr(min.Float64)
				}
				if max.Valid {
					meta.Max = f64ptr(max.Float64)
				}
				if avg.Valid {
					meta.Avg = f64ptr(math.Round(avg.Float64*100) / 100)
				}
			}
		}

		info.Fields[f.Name] = meta
	}

	auditDuration(start, "schema", &Query{Entity: entityName})
	return info, nil
}

// ===========================================================================
// Helpers
// ===========================================================================

func f64ptr(v float64) *float64 {
	return &v
}

func auditDuration(start time.Time, tool string, q *Query) {
	dur := time.Since(start).Milliseconds()
	_ = GlobalAuditRecorder.RecordToolCall(context.Background(), &ToolCallRecord{
		ToolName:   tool,
		Entity:     q.Entity,
		TenantID:   q.TenantID,
		DurationMs: dur,
	})
}
