// Package configgen генерирует конфиг data-service из интроспекции БД.
//
// Берёт datasource.Schema (таблицы, колонки, FK) и превращает в готовый
// config.Config с entities, endpoint'ами и stats. Без custom_queries —
// их пишет клиент под свою бизнес-логику.
//
// Использование:
//
//	adapter := datasource.SqliteAdapter{}
//	conn, _ := adapter.Connect(ctx, "university.db")
//	schema, _ := adapter.Introspect(ctx, conn)
//	cfg := configgen.Generate(schema, datasourceConfig, nil)
//	json.NewEncoder(os.Stdout).Encode(cfg)
package configgen

import (
	"fmt"
	"sort"
	"strings"

	"github.com/agent-tutor/data-service/internal/config"
	"github.com/agent-tutor/data-service/internal/datasource"
)

// skipPrefixes — таблицы, начинающиеся с этих префиксов, исключаются.
// Можно расширять через Generate's skipPrefixes параметр.
var defaultSkipPrefixes = []string{
	"sqlite_",
	"pg_",
	"documents", // внутренняя таблица RAG
}

// isNameField возвращает true, если колонка похожа на поисковое имя.
// Критерий: тип string, название содержит name/last_name/first_name/title.
func isNameField(col datasource.Column) bool {
	lower := strings.ToLower(col.Name)
	return col.Type == datasource.TypeString &&
		(lower == "name" ||
			strings.HasSuffix(lower, "_name") ||
			strings.HasSuffix(lower, "_title") ||
			strings.HasPrefix(lower, "name"))
}

// canFindByID возвращает true, если у таблицы ровно одна PK-колонка.
func canFindByID(pk []string) bool {
	return len(pk) == 1
}

// findSearchField ищет колонку для поиска (первую подходящую).
func findSearchField(cols []datasource.Column) (datasource.Column, bool) {
	for _, c := range cols {
		if isNameField(c) {
			return c, true
		}
	}
	return datasource.Column{}, false
}

// Generate создаёт *config.Config из интроспекции схемы БД.
//
// Параметры:
//   - schema — результат Introspect адаптера
//   - ds — data_source часть конфига (driver + dsn)
//   - skipPrefixes — дополнительные префиксы для исключения таблиц (nil = только дефолтные)
func Generate(schema *datasource.Schema, ds config.DataSourceConfig, skipPrefixes []string) *config.Config {
	mergedSkip := append([]string{}, defaultSkipPrefixes...)
	mergedSkip = append(mergedSkip, skipPrefixes...)

	cfg := &config.Config{
		Version:    1,
		DataSource: ds,
	}

	entities := make([]config.Entity, 0)
	endpoints := make([]config.Endpoint, 0)
	counters := make([]config.Counter, 0)

	// Сортируем таблицы для детерминизма
	tables := append([]datasource.Table{}, schema.Tables...)
	sort.Slice(tables, func(i, j int) bool {
		return tables[i].Name < tables[j].Name
	})

	for _, tbl := range tables {
		if shouldSkip(tbl.Name, mergedSkip) {
			continue
		}

		entity := tableToEntity(tbl)
		entities = append(entities, entity)

		// get_by_id
		if canFindByID(tbl.PrimaryKey) {
			pkCol := tbl.PrimaryKey[0]
			endpoints = append(endpoints, config.Endpoint{
				Method: config.MethodGET,
				Path:   fmt.Sprintf("/%s/{%s}", entity.Name, pkCol),
				Op:     config.OpGetByID,
				Entity: entity.Name,
			})
		}

		// find (по name-полю) — он же fallback на список без параметра
		if searchCol, ok := findSearchField(tbl.Columns); ok {
			endpoints = append(endpoints, config.Endpoint{
				Method:      config.MethodGET,
				Path:        fmt.Sprintf("/%s", entity.Name),
				Op:          config.OpFind,
				Entity:      entity.Name,
				SearchField: searchCol.Name,
				QueryParam:  searchCol.Name,
			})
		}

		// stats
		counters = append(counters, config.Counter{
			Name:   entity.Name,
			Entity: entity.Name,
		})
	}

	// Системные эндпоинты
	endpoints = append(endpoints, config.Endpoint{
		Method: config.MethodGET,
		Path:   "/health",
		Op:     config.OpBuiltinHealth,
	})
	endpoints = append(endpoints, config.Endpoint{
		Method: config.MethodGET,
		Path:   "/stats",
		Op:     config.OpBuiltinStats,
	})

	cfg.Entities = entities
	cfg.Endpoints = endpoints
	cfg.Stats = &config.StatsConfig{Counters: counters}

	return cfg
}

// tableToEntity конвертирует datasource.Table → config.Entity.
func tableToEntity(tbl datasource.Table) config.Entity {
	fields := make([]config.EntityField, 0, len(tbl.Columns))
	pkSet := make(map[string]bool, len(tbl.PrimaryKey))
	for _, pk := range tbl.PrimaryKey {
		pkSet[pk] = true
	}

	for _, col := range tbl.Columns {
		nullable := col.Nullable
		isPK := pkSet[col.Name]
		fields = append(fields, config.EntityField{
			Name:       col.Name,
			Column:     col.Name,
			Type:       config.FieldType(col.Type),
			Nullable:   &nullable,
			PrimaryKey: &isPK,
			Description: col.Description,
		})
	}

	return config.Entity{
		Name:     tbl.Name,
		Table:    tbl.Name,
		IDColumn: firstPK(tbl.PrimaryKey),
		Fields:   fields,
	}
}

// firstPK возвращает первую PK-колонку или пустую строку.
func firstPK(pk []string) string {
	if len(pk) > 0 {
		return pk[0]
	}
	return ""
}

// shouldSkip проверяет, начинается ли имя с одного из skip-префиксов.
func shouldSkip(name string, prefixes []string) bool {
	for _, p := range prefixes {
		if strings.HasPrefix(name, p) {
			return true
		}
	}
	return false
}
