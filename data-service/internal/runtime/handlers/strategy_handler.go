package handlers

import (
	"context"
	"database/sql"
	"fmt"
	"log/slog"
	"net/http"
	"strconv"
	"strings"

	"github.com/trash2bin/helperium/data-service/internal/query"
	"github.com/trash2bin/helperium/data-service/internal/runtime"
	"github.com/trash2bin/helperium/data-service/internal/search"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// NewStrategyHandler creates a generic HTTP handler for any search.Strategy.
//
// Flow:
//  1. Resolve entity via c.Resolver
//  2. Strategy parses HTTP request into query.QueryPlan
//  3. query.Engine builds SQL (+ tenant filter where possible)
//  4. COUNT + SELECT execution
//  5. Row mapping via c.Builder.MapRow + query.FormatRows
//
// Tenant row-level isolation:
//   - For []Condition-based plans: injected into the WHERE clause
//   - For RawWhere plans (grep with multi-token AND): wrapped in a
//     subquery to ensure tenant filter is always applied.
func NewStrategyHandler(c *Context, strategy search.Strategy, entityName string, entityCfg config.Entity) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		qCtx, qCancel := c.queryCtx(r)
		if qCancel != nil {
			defer qCancel()
		}
		entity, ok := c.Resolver.Resolve(entityName)
		if !ok {
			RespondError(w, http.StatusInternalServerError, "config_error", "entity not found")
			return
		}

		// Bridge runtime.AdapterSubset => query.AdapterSubset
		qAdapter := &runtime.AdapterToQuery{Inner: c.Adapter}
		searchAdapter := search.NewAdapter(qAdapter)

		plan, err := strategy.ParseRequest(r, entityCfg, searchAdapter)
		if err != nil {
			RespondError(w, http.StatusBadRequest, "parse_error", err.Error())
			return
		}

		engine := query.NewEngine(qAdapter)
		translate := asPlaceholderFunc(c.Adapter)

		if plan.Format == query.FormatCount {
			sqlStr, args, err := engine.BuildCount(*plan)
			if err != nil {
				RespondError(w, http.StatusInternalServerError, "query_error", err.Error())
				return
			}
			// Tenant-фильтр: existingArgCount = число уже сгенерированных args
			// (иначе на PG плейсхолдер tenant $1 коллизирует с WHERE $1..$n).
			tenantWhere, tenantArgs := tenantFilter(entityName, c.Auth, c.tenantID(r), len(args), translate)
			if tenantWhere != "" {
				// Применяем tenant как AND к ВНУТРЕННЕМУ WHERE, а не оборачиваем
				// агрегат в подзапрос: SELECT COUNT(*) не имеет колонки tenant_id,
				// внешний WHERE по ней дал бы "no such column" → 500.
				if strings.Contains(strings.ToUpper(sqlStr), " WHERE ") {
					sqlStr += " AND " + tenantWhere
				} else {
					sqlStr += " WHERE " + tenantWhere
				}
				args = append(args, tenantArgs...)
			}
			rows, err := c.DB.QueryContext(qCtx, sqlStr, args...)
			if err != nil {
				slog.Error("DB error in strategy handler count", "err", err, "strategy", strategy.Name(), "entity", entityName)
				RespondError(w, http.StatusInternalServerError, "db_error",
					"Query execution failed. Check field names via schema tool.")
				return
			}
			defer rows.Close() //nolint:errcheck
			var count int
			if rows.Next() {
				_ = rows.Scan(&count)
			}
			RespondJSON(w, http.StatusOK, map[string]any{
				"entity": entityName,
				"count":  count,
			})
			return
		}

		// Build the SELECT query
		sqlStr, args, err := engine.Build(*plan)
		if err != nil {
			RespondError(w, http.StatusInternalServerError, "query_error", err.Error())
			return
		}

		// Tenant-фильтр (row-level isolation). ВАЖНО: плейсхолдер tenant должен
		// быть пронумерован по ПОЗИЦИИ tenant-аргумента в финальном args.
		// - RawWhere-ветка: tenant идёт в КОНЕЦ (после innerArgs) → offset=len(innerArgs).
		// - Condition-ветка: tenant вставляется ПЕРЕД LIMIT/OFFSET → offset=
		//   len(whereArgs) = len(args) - число LIMIT/OFFSET аргументов.
		// См. count.go:115 как эталон (tenant в конце → offset=len(args)).

		// Apply tenant filter
		if plan.RawWhere != "" {
			slog.Debug("strategy handler: wrapping RawWhere query in subquery for tenant filter",
				"strategy", strategy.Name(), "entity", entityName)
			// ВАЖНО: inner-подзапрос должен включать tenant_id в проекцию,
			// иначе внешний WHERE "tenant_id" = ? не увидит колонку
			// (SQLite молча возвращает 0 строк, когда колонка не в SELECT-списке
			// подзапроса). См. tenant_count_regression_test.go.
			innerPlan := *plan // копия — не мутируем оригинал (нужен для count)
			innerPlan.Select.Columns = ensureColumn(innerPlan.Select.Columns, tenantIDCol(entity, c.Adapter))
			innerSQL, innerArgs, err := engine.Build(innerPlan)
			if err != nil {
				RespondError(w, http.StatusInternalServerError, "query_error", err.Error())
				return
			}
			tenantWhere, tenantArgs := tenantFilter(entityName, c.Auth, c.tenantID(r), len(innerArgs), translate)
			if tenantWhere != "" {
				// Внешний SELECT — явный список колонок БЕЗ tenant_id.
				// Иначе внутренний ensureColumn(tenant_id) (нужен для WHERE)
				// протекает наружу через SELECT *, и MapRow включает системную
				// колонку tenant_id в JSON-ответ (L2: загрязнение ответа).
				outerCols := make([]string, 0, len(plan.Select.Columns))
				for _, col := range plan.Select.Columns {
					if strings.Contains(col, "tenant_id") {
						continue
					}
					outerCols = append(outerCols, col)
				}
				if len(outerCols) == 0 {
					outerCols = []string{"*"}
				}
				sqlStr = "SELECT " + strings.Join(outerCols, ", ") + " FROM (" + innerSQL + ") AS _t WHERE " + tenantWhere
				args = innerArgs
				args = append(args, tenantArgs...)
			}
		} else if len(plan.Where) > 0 {
			// Condition-ветка: tenant-клауза вставляется перед LIMIT/OFFSET.
			// Плейсхолдер tenant = len(whereArgs)+1 (после WHERE-аргументов).
			// args переставляются: WHERE args + tenant args + LIMIT/OFFSET args.
			// Для PG хвостовые плейсхолдеры (LIMIT/OFFSET) перенумеровываются +1.
			whereArgCount := len(args)
			upper := strings.ToUpper(sqlStr)
			if strings.Contains(upper, " OFFSET ") {
				whereArgCount--
			}
			if strings.Contains(upper, " LIMIT ") {
				whereArgCount--
			}
			if whereArgCount < 0 {
				whereArgCount = 0
			}
			tenantWhere, tenantArgs := tenantFilter(entityName, c.Auth, c.tenantID(r), whereArgCount, translate)
			if tenantWhere != "" {
				isPG := adapterIsPostgres(c.Adapter)
				sqlStr, args = insertTenantBeforeLimit(sqlStr, args, " AND "+tenantWhere, tenantArgs, isPG)
			}
		} else {
			// Нет WHERE-условий (кроме tenant) — tenant offset = 0 (первый аргумент),
			// хвостовые плейсхолдеры (LIMIT/OFFSET) сдвигаются на 1 для PG.
			tenantWhere, tenantArgs := tenantFilter(entityName, c.Auth, c.tenantID(r), 0, translate)
			if tenantWhere != "" {
				isPG := adapterIsPostgres(c.Adapter)
				sqlStr, args = insertTenantBeforeLimit(sqlStr, args, " WHERE "+tenantWhere, tenantArgs, isPG)
			}
		}

		// Count for pagination.
		// C1-fix: для RawWhere-планов count строится из ОРИГИНАЛЬНОГО плана
		// (engine.BuildCount, без LIMIT/OFFSET), затем оборачивается в подзапрос
		// и tenant-фильтр применяется к count отдельно. countQueryWithArgs после
		// tenant-обёртки ломал SQL: strings.Index(" FROM ") брал внутреннее FROM,
		// а LastIndex(" LIMIT ") резал всё после LIMIT включая ") AS _t WHERE ..."
		// → незакрытая скобка → runCountQuery возвращал -1 (total=-1 у grep в multi-tenant).
		var countSQL string
		var countArgs []any
		if plan.RawWhere != "" {
			// count: SELECT COUNT(*) FROM t WHERE (RawWhere) AND tenant.
			// НЕ оборачиваем BuildCount в подзапрос — тот уже SELECT COUNT(*),
			// и у него нет колонки tenant_id для внешнего WHERE.
			// Плейсхолдер tenant считаем от len(plan.RawWhereArgs) — в count
			// участвуют только RawWhere-аргументы (не полный SELECT args).
			countTenantWhere, countTenantArgs := tenantFilter(entityName, c.Auth, c.tenantID(r), len(plan.RawWhereArgs), translate)
			if countTenantWhere != "" {
				countSQL = "SELECT COUNT(*) FROM " + plan.From + " WHERE (" + plan.RawWhere + ") AND " + countTenantWhere
				countArgs = append(append([]any{}, plan.RawWhereArgs...), countTenantArgs...)
			} else {
				countSQL = "SELECT COUNT(*) FROM " + plan.From + " WHERE (" + plan.RawWhere + ")"
				countArgs = append([]any{}, plan.RawWhereArgs...)
			}
		} else {
			countSQL, countArgs = countQueryWithArgs(sqlStr, args)
		}

		total := runCountQuery(qCtx, c.DB, countSQL, countArgs)

		// Execute SELECT
		rows, err := c.DB.QueryContext(qCtx, sqlStr, args...)
		if err != nil {
			slog.Error("DB error in strategy handler", "err", err, "strategy", strategy.Name(), "entity", entityName)
			RespondError(w, http.StatusInternalServerError, "db_error",
				"Query execution failed. Check field names via schema tool.")
			return
		}
		defer rows.Close() //nolint:errcheck

		results, err := c.Builder.MapRows(rows, func(rows *sql.Rows) (map[string]any, error) {
			return c.Builder.MapRow(rows, entity)
		}, 10000)
		if err != nil {
			RespondError(w, http.StatusInternalServerError, "mapping_error", err.Error())
			return
		}

		result := query.FormatRows(results, total, plan.Format, strategy.EntityIDCol(), strategy.EntityNameCol())

		// If no results, add LLM hint with available distinct values
		if total == 0 {
			hint := collectEmptyHint(qCtx, c.DB, entityCfg, searchAdapter)
			if hint != nil {
				result.EmptyHint = hint
			}
		}

		RespondJSON(w, http.StatusOK, result)
	}
}

// collectEmptyHint builds a hint for the LLM when search returns zero results.
// For each string field, it fetches up to 5 distinct values.
func collectEmptyHint(ctx context.Context, db runtime.AdapterSubset, entity config.Entity, a search.Adapter) *query.EmptyHint {
	if entity.Name == "" {
		return nil
	}

	qTable := a.QuoteIdentifier(entity.Table)
	suggested := fmt.Sprintf("Try schema_%s() to discover available values, then retry with exact values.", entity.Name)

	hint := &query.EmptyHint{
		SuggestedAction: suggested,
		AvailableValues: make(map[string][]string),
	}

	for _, f := range entity.Fields {
		if f.PrimaryKey != nil && *f.PrimaryKey {
			continue
		}
		if f.Type != config.FieldTypeString {
			continue
		}
		if f.Column == "tenant_id" {
			continue
		}
		if f.ExcludeFromSearch {
			continue
		}

		qCol := a.QuoteIdentifier(f.Column)
		distinctSQL := fmt.Sprintf("SELECT DISTINCT %s FROM %s WHERE %s IS NOT NULL ORDER BY %s LIMIT 5", qCol, qTable, qCol, qCol)

		rows, err := db.QueryContext(ctx, distinctSQL)
		if err != nil {
			slog.Debug("collectEmptyHint: query failed", "field", f.Name, "err", err)
			continue
		}

		var vals []string
		for rows.Next() {
			var v string
			if err := rows.Scan(&v); err != nil {
				continue
			}
			vals = append(vals, v)
		}
		rows.Close() //nolint:errcheck

		if len(vals) > 0 {
			hint.AvailableValues[f.Name] = vals
		}
	}

	if len(hint.AvailableValues) == 0 {
		return nil
	}
	return hint
}

// insertTenantBeforeLimit inserts a SQL fragment before the first of
// ORDER BY / LIMIT / OFFSET clauses and reorders args so that WHERE args,
// tenant args, and LIMIT/OFFSET args appear in the correct order.
//
// Вставка перед LIMIT (как было) ломала SQL при sort_by: tenant-клауза
// оказывалась ПОСЛЕ ORDER BY → "ORDER BY x DESC AND tenant..." — синтаксическая
// ошибка. Вставляем перед МИНИМАЛЬНЫМ индексом из трёх клауз.
//
// isPostgres: для PG ($N) хвостовые плейсхолдеры (LIMIT/OFFSET) после точки
// вставки перенумеровываются +1 (tenant занял следующий номер). Для SQLite
// (?) — позиция аргумента, args переставляются (where + tenant + limit/offset).
func insertTenantBeforeLimit(sql string, args []any, tenantClause string, tenantArgs []any, isPostgres bool) (string, []any) {
	upper := strings.ToUpper(sql)
	orderIdx := strings.LastIndex(upper, " ORDER BY ")
	limitIdx := strings.LastIndex(upper, " LIMIT ")
	offsetIdx := strings.LastIndex(upper, " OFFSET ")

	// Минимальный положительный индекс — позиция вставки tenant-клаузы.
	insertIdx := -1
	for _, idx := range []int{orderIdx, limitIdx, offsetIdx} {
		if idx >= 0 && (insertIdx == -1 || idx < insertIdx) {
			insertIdx = idx
		}
	}

	// Count how many trailing args belong to LIMIT/OFFSET
	limitOffsetCount := 0
	if offsetIdx >= 0 {
		limitOffsetCount++ // OFFSET arg
	}
	if limitIdx >= 0 {
		limitOffsetCount++ // LIMIT arg
	}

	// Split args: WHERE args vs LIMIT/OFFSET args
	whereArgsLen := len(args) - limitOffsetCount
	if whereArgsLen < 0 {
		whereArgsLen = 0
	}
	whereArgs := args[:whereArgsLen]
	limitOffsetArgs := args[whereArgsLen:]

	// Rebuild: WHERE args + tenant args + LIMIT/OFFSET args
	newArgs := make([]any, 0, len(args)+len(tenantArgs))
	newArgs = append(newArgs, whereArgs...)
	newArgs = append(newArgs, tenantArgs...)
	newArgs = append(newArgs, limitOffsetArgs...)

	// Insert tenant clause before the first ORDER BY/LIMIT/OFFSET.
	var newSQL string
	if insertIdx >= 0 {
		newSQL = sql[:insertIdx] + tenantClause + sql[insertIdx:]
	} else {
		newSQL = sql + tenantClause
	}

	// Для PG: хвостовые плейсхолдеры ($N) после точки вставки сдвигаем на 1.
	// tenantClause уже содержит $len(whereArgs)+1; LIMIT/OFFSET были
	// $len(whereArgs)+2/+3 и должны стать +3/+4.
	if isPostgres && insertIdx >= 0 && limitOffsetCount > 0 {
		newSQL = renumberPGPlaceholdersAfter(newSQL, insertIdx+len(tenantClause))
	}

	return newSQL, newArgs
}

// renumberPGPlaceholdersAfter увеличивает номера всех PG-плейсхолдеров ($N)
// в SQL, начиная с позиции startIdx (после точки вставки tenant-клаузы), на 1.
func renumberPGPlaceholdersAfter(sql string, startIdx int) string {
	var sb strings.Builder
	sb.Grow(len(sql) + 8)
	sb.WriteString(sql[:startIdx])
	for i := startIdx; i < len(sql); i++ {
		ch := sql[i]
		if ch == '$' && i+1 < len(sql) && sql[i+1] >= '0' && sql[i+1] <= '9' {
			sb.WriteByte('$')
			// читаем номер
			j := i + 1
			for j < len(sql) && sql[j] >= '0' && sql[j] <= '9' {
				j++
			}
			num := 0
			for k := i + 1; k < j; k++ {
				num = num*10 + int(sql[k]-'0')
			}
			sb.WriteString(strconv.Itoa(num + 1))
			i = j - 1
			continue
		}
		sb.WriteByte(ch)
	}
	return sb.String()
}

// adapterIsPostgres определяет PG-стиль плейсхолдеров ($N) по адаптеру.
func adapterIsPostgres(a runtime.AdapterSubset) bool {
	if a == nil {
		return false
	}
	return a.TranslatePlaceholder(1) == "$1"
}

// ensureColumn добавляет колонку в список, если её там нет.
func ensureColumn(cols []string, col string) []string {
	for _, c := range cols {
		if c == col {
			return cols
		}
	}
	return append(cols, col)
}

// tenantIDCol возвращает квотированное имя колонки tenant_id.
func tenantIDCol(entity runtime.Entity, adapter runtime.AdapterSubset) string {
	if adapter == nil {
		return `"tenant_id"`
	}
	return adapter.QuoteIdentifier("tenant_id")
}
