package configgen

import (
	"fmt"
	"log/slog"
	"regexp"
	"strings"

	"github.com/trash2bin/helperium/helperium-go/config"
)

// safeIdentRe — допустимые идентификаторы для встраивания в SQL без квотирования.
// navigation.go генерирует custom_query SQL напрямую (SELECT t.* FROM %s t WHERE t.%s = ?)
// без QuoteIdentifier (в runtime BuildCustomQuery не квотирует идентификаторы).
// Имена с спецсимволами (пробелы, `"`, `;`, дефисы) сломают SQL или позволят
// инъекцию из имени БД — такие связи пропускаем с логом.
var safeIdentRe = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)

// quoteTableRef возвращает безопасную SQL-ссылку на таблицу.
//
// Для простых имён (products) — как есть (проходят safeIdentRe, SQLite-совместимо).
// Для schema-qualified PG (public.brands) — квотирует каждый сегмент:
// "public"."brands". Runtime BuildCustomQuery не квотирует, поэтому встраиваем
// квоты здесь. Имена с небезопасными символами (пробелы, `"`, `;`, дефисы)
// в любом сегменте → возвращает "" (связь пропускается, смотри caller).
func quoteTableRef(table string) string {
	if safeIdentRe.MatchString(table) {
		return table
	}
	// schema-qualified: все сегменты должны быть безопасными идентификаторами
	if strings.Contains(table, ".") {
		segs := strings.Split(table, ".")
		quoted := make([]string, 0, len(segs))
		for _, s := range segs {
			if !safeIdentRe.MatchString(s) {
				return ""
			}
			quoted = append(quoted, `"`+s+`"`)
		}
		return strings.Join(quoted, ".")
	}
	return ""
}

// buildNavigationEndpoints generates custom queries and navigation endpoints
// from FK relations. Creates GET /parent/{id}/child endpoints for each FK.
func buildNavigationEndpoints(entities []config.Entity) ([]config.Endpoint, map[string]config.CustomQuery) {
	customQueries := make(map[string]config.CustomQuery)
	var endpoints []config.Endpoint

	for _, entity := range entities {
		for _, rel := range entity.Relations {
			// rel.Table — parent таблица (куда ссылается FK)
			// rel.LocalFK — колонка FK в текущей (child) таблице
			// rel.Kind = many_to_one: child.fk → parent.id
			//
			// Navigation endpoint: GET /parent/{id}/child_table
			// "Show me all children for a given parent"

			// Валидируем идентификаторы, встраиваемые в SQL без квотирования.
			// Для PostgreSQL таблица может быть schema-qualified (public.brands):
			// пропускаем такие связи целиком — навигационные запросы не умеют
			// безопасно квотировать schema.table, лучше не генерить битый SQL.
			if !safeIdentRe.MatchString(rel.LocalFK) {
				slog.Warn("navigation: skipping relation with unsafe FK column",
					"entity", entity.Name, "fk", rel.LocalFK)
				continue
			}

			// Находим parent entity по имени таблицы
			var parentEntity *config.Entity
			for j := range entities {
				if entities[j].Table == rel.Table || entities[j].Name == rel.Table {
					parentEntity = &entities[j]
					break
				}
			}
			if parentEntity == nil {
				continue
			}

			// ID колонка parent'а для {id} в URL
			parentID := parentEntity.IDColumn
			if parentID == "" {
				continue
			}

			// custom_query ID: {child_table}_by_{parent_table}_{fk_column}
			queryID := fmt.Sprintf("%s_by_%s_%s", entity.Name, parentEntity.Name, rel.LocalFK)
			if _, exists := customQueries[queryID]; exists {
				continue
			}

			// Имена child-таблицы и FK-колонки идут в SQL без квотирования —
			// требуем безопасный набор символов (см. safeIdentRe).
			// Schema-qualified PG-имена (public.brands) НЕ проходят safeIdentRe,
			// но их можно безопасно квотировать по сегментам ("public"."brands").
			// Runtime BuildCustomQuery не квотирует, поэтому встраиваем квоты здесь.
			tableRef := quoteTableRef(entity.Table)
			if tableRef == "" {
				slog.Warn("navigation: skipping relation with unsafe table name",
					"entity", entity.Name, "table", entity.Table)
				continue
			}

			// SELECT * FROM child_table WHERE fk = ?
			customQueries[queryID] = config.CustomQuery{
				SQL:         fmt.Sprintf("SELECT t.* FROM %s t WHERE t.%s = ?", tableRef, rel.LocalFK),
				Params:      []string{rel.LocalFK},
				MaxRows:     1000,
				Description: fmt.Sprintf("All %s linked to a %s", entity.Name, parentEntity.Name),
			}

			// Navigation endpoint: GET /parent/{id}/child
			navPath := fmt.Sprintf("/%s/{%s}/%s", parentEntity.Name, parentID, entity.Name)
			// Проверяем дубликат
			dup := false
			for _, ep := range endpoints {
				if ep.Path == navPath && ep.Op == config.OpCustomQuery {
					dup = true
					break
				}
			}
			if !dup {
				required := true
				endpoints = append(endpoints, config.Endpoint{
					Method:      config.MethodGET,
					Path:        navPath,
					Op:          config.OpCustomQuery,
					QueryID:     queryID,
					Entity:      entity.Name,
					Description: fmt.Sprintf("All %s for a given %s", entity.Name, parentEntity.Name),
					Params: []config.EndpointParam{
						{
							Name:        parentID,
							In:          config.ParamInPath,
							Type:        config.ParamTypeString,
							Required:    &required,
							Description: fmt.Sprintf("ID of %s", parentEntity.Name),
						},
					},
				})
			}
		}
	}

	return endpoints, customQueries
}
