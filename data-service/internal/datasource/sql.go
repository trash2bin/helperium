package datasource

import (
	"context"
	"database/sql"
	"fmt"
	"log"
	"math"
	"strings"
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

// ── entity whitelist ───────────────────────────────────────────────────────

func (s *SQLDataSource) entity(name string) (config.Entity, error) {
	e, ok := s.entityMap[name]
	if !ok {
		return e, fmt.Errorf("entity %q not found", name)
	}
	return e, nil
}

// searchableStringFields возвращает строковые поля для текстового поиска
// (исключая PK и tenant_id).
func searchableStringFields(entity config.Entity) []config.EntityField {
	var result []config.EntityField
	for _, f := range entity.Fields {
		if f.PrimaryKey != nil && *f.PrimaryKey {
			continue
		}
		if f.Column == "tenant_id" {
			continue
		}
		if f.ExcludeFromSearch {
			continue
		}
		if f.Type == config.FieldTypeString {
			result = append(result, f)
		}
	}
	return result
}

// filterableFields возвращает все поля для фильтрации (исключая PK, tenant_id, ExcludeFromSearch).
func filterableFields(entity config.Entity) []config.EntityField {
	var result []config.EntityField
	for _, f := range entity.Fields {
		if f.PrimaryKey != nil && *f.PrimaryKey {
			continue
		}
		if f.Column == "tenant_id" {
			continue
		}
		if f.ExcludeFromSearch {
			continue
		}
		result = append(result, f)
	}
	return result
}

// findNameField возвращает первую строковую (не PK) для compact format.
func findNameField(entity config.Entity) string {
	for _, f := range entity.Fields {
		if f.PrimaryKey != nil && *f.PrimaryKey {
			continue
		}
		if f.Type == config.FieldTypeString {
			return f.Column
		}
	}
	return entity.IDColumnOrDefault()
}

// ── timeout helper ──────────────────────────────────────────────────────────

func (s *SQLDataSource) withTimeout(ctx context.Context) (context.Context, context.CancelFunc) {
	if s.timeout > 0 {
		return context.WithTimeout(ctx, s.timeout)
	}
	return ctx, func() {}
}

// ── result scanning ─────────────────────────────────────────────────────────

func scanRows(rows *sql.Rows) ([]map[string]any, error) {
	cols, err := rows.Columns()
	if err != nil {
		return nil, err
	}
	var results []map[string]any
	for rows.Next() {
		vals := make([]any, len(cols))
		ptrs := make([]any, len(cols))
		for i := range vals {
			ptrs[i] = &vals[i]
		}
		if err := rows.Scan(ptrs...); err != nil {
			return nil, err
		}
		row := make(map[string]any, len(cols))
		for i, c := range cols {
			val := vals[i]
			if b, ok := val.([]byte); ok {
				row[c] = string(b)
			} else {
				row[c] = val
			}
		}
		results = append(results, row)
	}
	return results, rows.Err()
}

// ── buildTenantCond ─────────────────────────────────────────────────────────

func (s *SQLDataSource) buildTenantCond(q *Query) []query.Condition {
	if q.TenantID == "" {
		return nil
	}
	return []query.Condition{
		{
			Field:    s.adapter.QuoteIdentifier("tenant_id"),
			Operator: query.OpEq,
			Value:    q.TenantID,
		},
	}
}

// ===========================================================================
// Search — текстовый поиск (grep)
// ===========================================================================

func (s *SQLDataSource) Search(ctx context.Context, q *Query) (*Result, error) {
	start := time.Now()
	ctx, cancel := s.withTimeout(ctx)
	defer cancel()

	entity, err := s.entity(q.Entity)
	if err != nil {
		return nil, err
	}
	if q.Pattern == "" {
		return nil, fmt.Errorf("pattern required for search")
	}

	// Шаг 1: строим SQL через query.Engine
	a := s.adapter
	fields := searchableStringFields(entity)
	if len(fields) == 0 {
		return nil, fmt.Errorf("entity %q has no text-searchable fields", q.Entity)
	}

	// multi-token AND, multi-field OR — через RawWhere (временно, пока нет CompositeCondition)
	pattern := strings.TrimSpace(q.Pattern)
	tokens := strings.Fields(pattern)
	phIdx := 1

	var whereClauses []string
	var args []any
	likeOp := "LIKE"
	if a.TranslatePlaceholder(1) != "?" { // postgres
		likeOp = "ILIKE"
	}

	for _, field := range fields {
		qName := a.QuoteIdentifier(field.Column)
		tokenClauses := make([]string, 0, len(tokens))
		for _, tok := range tokens {
			escaped := a.QuoteString(tok)
			val := "%" + escaped + "%"
			ph := a.TranslatePlaceholder(phIdx)
			phIdx++
			tokenClauses = append(tokenClauses, qName+" "+likeOp+" "+ph)
			args = append(args, val)
		}
		whereClauses = append(whereClauses, "("+strings.Join(tokenClauses, " AND ")+")")
	}

	rawWhere := strings.Join(whereClauses, " OR ")
	if len(rawWhere) > 0 {
		rawWhere = "(" + rawWhere + ")"
	}

	// Tenant isolation — встраивается сервером, не LLM
	tenantConds := s.buildTenantCond(q)
	if len(tenantConds) > 0 {
		tc := tenantConds[0]
		rawWhere += " AND " + tc.Field + " = " + a.TranslatePlaceholder(phIdx)
		args = append(args, tc.Value)
		phIdx++
	}

	// Параметры пагинации
	limit := q.Limit
	if limit <= 0 || limit > 100 {
		limit = 10
	}
	offset := q.Offset
	if offset < 0 {
		offset = 0
	}

	// SELECT
	idCol := a.QuoteIdentifier(entity.IDColumnOrDefault())
	nameCol := a.QuoteIdentifier(findNameField(entity))

	if q.Format == FormatFull {
		var allCols []string
		for _, f := range entity.Fields {
			if f.Column == "tenant_id" {
				continue
			}
			allCols = append(allCols, a.QuoteIdentifier(f.Column))
		}
		sqlStr := fmt.Sprintf("SELECT %s FROM %s WHERE %s LIMIT %s OFFSET %s",
			strings.Join(allCols, ", "),
			a.QuoteIdentifier(entity.Table),
			rawWhere,
			a.TranslatePlaceholder(phIdx),
			a.TranslatePlaceholder(phIdx+1))
		allArgs := append([]any{}, args...)
		allArgs = append(allArgs, limit, offset)
		log.Printf("[SQLDataSource] Search(full): %s %v", sqlStr, allArgs)

		rows, err := s.db.QueryContext(ctx, sqlStr, allArgs...)
		if err != nil {
			log.Printf("DB error in Search: %v", err)
			return nil, fmt.Errorf("query execution failed")
		}
		defer rows.Close() //nolint:errcheck

		data, err := scanRows(rows)
		if err != nil {
			log.Printf("scan error in Search: %v", err)
			return nil, fmt.Errorf("query execution failed")
		}

		auditDuration(start, "search", q)
		return &Result{Total: len(data), Returned: len(data), Data: data}, nil
	}

	// Compact format
	sqlStr := fmt.Sprintf("SELECT %s, %s FROM %s WHERE %s LIMIT %s OFFSET %s",
		idCol, nameCol,
		a.QuoteIdentifier(entity.Table),
		rawWhere,
		a.TranslatePlaceholder(phIdx),
		a.TranslatePlaceholder(phIdx+1))
	allArgs := append([]any{}, args...)
	allArgs = append(allArgs, limit, offset)

	log.Printf("[SQLDataSource] Search: %s %v", sqlStr, allArgs)
	rows, err := s.db.QueryContext(ctx, sqlStr, allArgs...)
	if err != nil {
		log.Printf("DB error in Search: %v", err)
		return nil, fmt.Errorf("query execution failed")
	}
	defer rows.Close() //nolint:errcheck

	data, err := scanRows(rows)
	if err != nil {
		log.Printf("scan error in Search: %v", err)
		return nil, fmt.Errorf("query execution failed")
	}

	auditDuration(start, "search", q)
	return &Result{Total: len(data), Returned: len(data), Preview: data}, nil
}

// ===========================================================================
// Filter — field-based фильтрация
// ===========================================================================

func (s *SQLDataSource) Filter(ctx context.Context, q *Query) (*Result, error) {
	start := time.Now()
	ctx, cancel := s.withTimeout(ctx)
	defer cancel()

	entity, err := s.entity(q.Entity)
	if err != nil {
		return nil, err
	}
	if len(q.Filters) == 0 {
		return nil, fmt.Errorf("at least one filter required")
	}

	a := s.adapter
	fieldMap := make(map[string]config.EntityField)
	for _, f := range filterableFields(entity) {
		fieldMap[f.Name] = f
		fieldMap[f.Column] = f
	}

	// Проверяем что колонка tenant_id не маппится
	delete(fieldMap, "tenant_id")

	var conditions []query.Condition

	for _, f := range q.Filters {
		ef, ok := fieldMap[f.Field]
		if !ok {
			continue
		}
		qName := a.QuoteIdentifier(ef.Column)

		switch f.Operator {
		case "eq":
			conditions = append(conditions, query.Condition{
				Field: qName, Operator: query.OpEq, Value: f.Value,
			})
		case "neq":
			conditions = append(conditions, query.Condition{
				Field: qName, Operator: query.OpNeq, Value: f.Value,
			})
		case "gt":
			conditions = append(conditions, query.Condition{
				Field: qName, Operator: query.OpGt, Value: f.Value,
			})
		case "gte":
			conditions = append(conditions, query.Condition{
				Field: qName, Operator: query.OpGte, Value: f.Value,
			})
		case "lt":
			conditions = append(conditions, query.Condition{
				Field: qName, Operator: query.OpLt, Value: f.Value,
			})
		case "lte":
			conditions = append(conditions, query.Condition{
				Field: qName, Operator: query.OpLte, Value: f.Value,
			})
		case "like":
			conditions = append(conditions, query.Condition{
				Field: qName, Operator: query.OpILike, Value: f.Value,
			})
		case "in":
			conditions = append(conditions, query.Condition{
				Field: qName, Operator: query.OpIn, Values: f.Values,
			})
		}
	}

	tenantConds := s.buildTenantCond(q)
	conditions = append(conditions, tenantConds...)

	limit := q.Limit
	if limit <= 0 || limit > 100 {
		limit = 10
	}
	offset := q.Offset
	if offset < 0 {
		offset = 0
	}

	plan := query.QueryPlan{
		Select: query.SelectClause{
			Columns: []string{
				a.QuoteIdentifier(entity.IDColumnOrDefault()),
				a.QuoteIdentifier(findNameField(entity)),
			},
		},
		From:   a.QuoteIdentifier(entity.Table),
		Where:  conditions,
		Limit:  limit,
		Offset: offset,
	}

	if q.Format == FormatFull {
		var allCols []string
		for _, f := range entity.Fields {
			if f.Column == "tenant_id" {
				continue
			}
			allCols = append(allCols, a.QuoteIdentifier(f.Column))
		}
		plan.Select.Columns = allCols
	}

	sqlStr, args, err := s.engine.Build(plan)
	if err != nil {
		log.Printf("Filter build error: %v", err)
		return nil, fmt.Errorf("query execution failed")
	}

	// Ручная корректировка placeholder'ов для LIMIT/OFFSET
	// т.к. Engine уже использовал phIdx на свой лад
	log.Printf("[SQLDataSource] Filter: %s %v", sqlStr, args)
	rows, err := s.db.QueryContext(ctx, sqlStr, args...)
	if err != nil {
		log.Printf("DB error in Filter: %v", err)
		return nil, fmt.Errorf("query execution failed")
	}
	defer rows.Close() //nolint:errcheck

	data, err := scanRows(rows)
	if err != nil {
		log.Printf("scan error in Filter: %v", err)
		return nil, fmt.Errorf("query execution failed")
	}

	auditDuration(start, "filter", q)
	return &Result{Total: len(data), Returned: len(data), Preview: data}, nil
}

// ===========================================================================
// GetByID — by primary key
// ===========================================================================

func (s *SQLDataSource) GetByID(ctx context.Context, entityName string, id any) (*Record, error) {
	start := time.Now()
	ctx, cancel := s.withTimeout(ctx)
	defer cancel()

	entity, err := s.entity(entityName)
	if err != nil {
		return nil, err
	}

	a := s.adapter
	idCol := a.QuoteIdentifier(entity.IDColumnOrDefault())
	ph := a.TranslatePlaceholder(1)

	var allCols []string
	for _, f := range entity.Fields {
		if f.Column == "tenant_id" {
			continue
		}
		allCols = append(allCols, a.QuoteIdentifier(f.Column))
	}

	sqlStr := fmt.Sprintf("SELECT %s FROM %s WHERE %s = %s LIMIT 1",
		strings.Join(allCols, ", "),
		a.QuoteIdentifier(entity.Table),
		idCol, ph)

	log.Printf("[SQLDataSource] GetByID: %s [id=%v]", sqlStr, id)
	rows2, err := s.db.QueryContext(ctx, sqlStr, id)
	if err != nil {
		log.Printf("DB error in GetByID: %v", err)
		return nil, fmt.Errorf("query execution failed")
	}
	defer rows2.Close() //nolint:errcheck

	cols := make([]string, len(allCols))
	for i, c := range allCols {
		cols[i] = strings.Trim(c, `"`)
	}
	vals := make([]any, len(cols))
	ptrs := make([]any, len(cols))
	for i := range vals {
		ptrs[i] = &vals[i]
	}

	if !rows2.Next() {
		return nil, fmt.Errorf("not found")
	}
	if err := rows2.Scan(ptrs...); err != nil {
		log.Printf("DB error in GetByID: %v", err)
		return nil, fmt.Errorf("query execution failed")
	}

	fields := make(map[string]any, len(cols))
	for i, c := range cols {
		if b, ok := vals[i].([]byte); ok {
			fields[c] = string(b)
		} else {
			fields[c] = vals[i]
		}
	}

	auditDuration(start, "get_by_id", &Query{Entity: entityName})
	return &Record{Fields: fields}, nil
}

// ===========================================================================
// Count
// ===========================================================================

func (s *SQLDataSource) Count(ctx context.Context, q *Query) (int64, error) {
	start := time.Now()
	ctx, cancel := s.withTimeout(ctx)
	defer cancel()

	entity, err := s.entity(q.Entity)
	if err != nil {
		return 0, err
	}

	a := s.adapter
	var conditions []query.Condition

	fieldMap := make(map[string]config.EntityField)
	for _, f := range filterableFields(entity) {
		fieldMap[f.Name] = f
		fieldMap[f.Column] = f
	}
	delete(fieldMap, "tenant_id")

	for _, f := range q.Filters {
		ef, ok := fieldMap[f.Field]
		if !ok {
			continue
		}
		qName := a.QuoteIdentifier(ef.Column)
		switch f.Operator {
		case "eq":
			conditions = append(conditions, query.Condition{Field: qName, Operator: query.OpEq, Value: f.Value})
		case "neq":
			conditions = append(conditions, query.Condition{Field: qName, Operator: query.OpNeq, Value: f.Value})
		case "gt":
			conditions = append(conditions, query.Condition{Field: qName, Operator: query.OpGt, Value: f.Value})
		case "gte":
			conditions = append(conditions, query.Condition{Field: qName, Operator: query.OpGte, Value: f.Value})
		case "lt":
			conditions = append(conditions, query.Condition{Field: qName, Operator: query.OpLt, Value: f.Value})
		case "lte":
			conditions = append(conditions, query.Condition{Field: qName, Operator: query.OpLte, Value: f.Value})
		case "like":
			conditions = append(conditions, query.Condition{Field: qName, Operator: query.OpILike, Value: f.Value})
		case "in":
			conditions = append(conditions, query.Condition{Field: qName, Operator: query.OpIn, Values: f.Values})
		}
	}

	tenantConds := s.buildTenantCond(q)
	conditions = append(conditions, tenantConds...)

	plan := query.QueryPlan{
		From:  a.QuoteIdentifier(entity.Table),
		Where: conditions,
	}

	sqlStr, args, err := s.engine.BuildCount(plan)
	if err != nil {
		log.Printf("Count build error: %v", err)
		return 0, fmt.Errorf("query execution failed")
	}

	log.Printf("[SQLDataSource] Count: %s %v", sqlStr, args)
	countRows, err := s.db.QueryContext(ctx, sqlStr, args...)
	if err != nil {
		log.Printf("DB error in Count: %v", err)
		return 0, fmt.Errorf("query execution failed")
	}
	defer countRows.Close() //nolint:errcheck
	var count int64
	if countRows.Next() {
		_ = countRows.Scan(&count)
	}

	auditDuration(start, "count", q)
	return count, nil
}

// ===========================================================================
// Distinct
// ===========================================================================

func (s *SQLDataSource) Distinct(ctx context.Context, entityName, column string) ([]string, error) {
	start := time.Now()
	ctx, cancel := s.withTimeout(ctx)
	defer cancel()

	entity, err := s.entity(entityName)
	if err != nil {
		return nil, err
	}

	// Whitelist: находим колонку в entity.Fields
	var foundCol string
	for _, f := range entity.Fields {
		if f.Column == "tenant_id" {
			continue
		}
		if f.Name == column || f.Column == column {
			foundCol = f.Column
			break
		}
	}
	if foundCol == "" {
		return nil, fmt.Errorf("column %q not found in entity %q", column, entityName)
	}

	a := s.adapter
	qName := a.QuoteIdentifier(foundCol)

	sqlStr := fmt.Sprintf("SELECT DISTINCT %s FROM %s WHERE %s IS NOT NULL AND %s != '' ORDER BY %s LIMIT 50",
		qName, a.QuoteIdentifier(entity.Table), qName, qName, qName)

	log.Printf("[SQLDataSource] Distinct: %s", sqlStr)
	rows, err := s.db.QueryContext(ctx, sqlStr)
	if err != nil {
		log.Printf("DB error in Distinct: %v", err)
		return nil, fmt.Errorf("query execution failed")
	}
	defer rows.Close() //nolint:errcheck

	var results []string
	for rows.Next() {
		var val string
		if err := rows.Scan(&val); err != nil {
			continue
		}
		results = append(results, val)
	}

	auditDuration(start, "distinct", &Query{Entity: entityName})
	return results, nil
}

// ===========================================================================
// Schema
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
