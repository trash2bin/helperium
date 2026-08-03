package handlers

import (
	"log/slog"
	"net/http"

	"github.com/trash2bin/helperium/data-service/internal/search"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// ── /q/* dispatch layer (Фаза 2) ────────────────────────────────────────
//
// Консолидированные LLM-тулы: db_map / db_describe / db_search / db_get /
// db_related. Фильтрация — пер-энтити filter_{entity} (Фаза 2.5, живой REST).
// Консолидированные не зависят от числа сущностей (5 константных).
//
// Все хендлеры резолвят entity через EntityResolver.Resolve — это
// whitelist-граница (произвольную таблицу не открыть). Параметр entity
// ОБЯЗАТЕЛЬНО стрипается из query перед делегированием стратегии, чтобы
// filter не принял его за поле.

// SchemaForLLMCallback возвращает SchemaForLLM (карту БД для модели).
// Колбэк ставится сервером, где есть доступ к introspected schema (TenantInstance).
type SchemaForLLMCallback func(r *http.Request) (any, bool)

// QMapHandler — GET /q/map → SchemaForLLM (карта БД: сущности, поля, hints).
func QMapHandler(getSchema SchemaForLLMCallback) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		schema, ok := getSchema(r)
		if !ok {
			RespondError(w, http.StatusServiceUnavailable, "schema_not_available",
				"schema not yet introspected — please call POST /admin/config/rewrite first")
			return
		}
		RespondJSON(w, http.StatusOK, schema)
	}
}

// QDescribeHandler — GET /q/describe?entity=X → SchemaStrategy (метаданные сущности).
func QDescribeHandler(c *Context, entityResolver EntityResolverFunc, makeSchema func(entityName string) http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		entityName := stripEntityParam(r)
		if entityName == "" {
			RespondError(w, http.StatusBadRequest, "missing_entity", "entity parameter is required")
			return
		}
		if !entityResolver(entityName) {
			RespondError(w, http.StatusNotFound, "unknown_entity",
				"unknown entity. Call db_map to see available entities.")
			return
		}
		makeSchema(entityName)(w, r)
	}
}

// QSearchHandler — GET /q/search?entity=X&pattern=... → GrepStrategy.
// Параметр entity стрипается из query перед делегированием.
func QSearchHandler(c *Context, entityResolver EntityResolverFunc, makeGrep func(entityName string) http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		entityName := stripEntityParam(r)
		if entityName == "" {
			RespondError(w, http.StatusBadRequest, "missing_entity", "entity parameter is required")
			return
		}
		if !entityResolver(entityName) {
			RespondError(w, http.StatusNotFound, "unknown_entity",
				"unknown entity. Call db_map to see available entities.")
			return
		}
		makeGrep(entityName)(w, r)
	}
}

// QFilterHandler — GET /q/filter?entity=X&field__op=val → FilterStrategy.
func QFilterHandler(c *Context, entityResolver EntityResolverFunc, makeFilter func(entityName string) http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		entityName := stripEntityParam(r)
		if entityName == "" {
			RespondError(w, http.StatusBadRequest, "missing_entity", "entity parameter is required")
			return
		}
		if !entityResolver(entityName) {
			RespondError(w, http.StatusNotFound, "unknown_entity",
				"unknown entity. Call db_map to see available entities.")
			return
		}
		makeFilter(entityName)(w, r)
	}
}

// QGetHandler — GET /q/get?entity=X&id=... → GetByID.
// Анти-перебор: описание тула требует id из предыдущего поиска.
func QGetHandler(c *Context, entityResolver EntityResolverFunc, makeGet func(entityName string) http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		entityName := stripEntityParam(r)
		if entityName == "" {
			RespondError(w, http.StatusBadRequest, "missing_entity", "entity parameter is required")
			return
		}
		if !entityResolver(entityName) {
			RespondError(w, http.StatusNotFound, "unknown_entity",
				"unknown entity. Call db_map to see available entities.")
			return
		}
		makeGet(entityName)(w, r)
	}
}

// QRelatedHandler — GET /q/related?entity=X&id=...&relation=... → FK-навигация.
// Возвращает строки текущей сущности, где FK (relation) == id.
// В отличие от custom_query navigation: применяет tenant-фильтр и лимиты.
func QRelatedHandler(c *Context, entityResolver EntityResolverFunc, makeRelated func(entityName string) http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		entityName := stripEntityParam(r)
		if entityName == "" {
			RespondError(w, http.StatusBadRequest, "missing_entity", "entity parameter is required")
			return
		}
		if !entityResolver(entityName) {
			RespondError(w, http.StatusNotFound, "unknown_entity",
				"unknown entity. Call db_map to see available entities.")
			return
		}
		makeRelated(entityName)(w, r)
	}
}

// EntityResolverFunc — проверяет существование entity по имени (whitelist).
type EntityResolverFunc func(name string) bool

// stripEntityParam извлекает и УДАЛЯЕТ параметр entity из query.
// Возвращает пустую строку, если entity не задан.
// Важно: параметр удаляется из r.URL.Query(), чтобы стратегия (filter/grep)
// не приняла его за поле. Go map iteration — мы пересоздаём query без entity.
func stripEntityParam(r *http.Request) string {
	q := r.URL.Query()
	entityName := q.Get("entity")
	if entityName == "" {
		return ""
	}
	q.Del("entity")
	r.URL.RawQuery = q.Encode()
	return entityName
}

// ── конструирование make* колбэков ─────────────────────────────────────

// MakeQDispatcher собирает /q/* хендлеры из Context и entity-провайдера.
// entityProvider отдаёт config.Entity по имени (для стратегий нужен config.Entity,
// не runtime.Entity). Возвращает мапу route → handler.
//
// routes:
//   /q/map       → QMapHandler
//   /q/describe  → QDescribeHandler
//   /q/search    → QSearchHandler
//   /q/filter    → QFilterHandler
//   /q/get       → QGetHandler
//   /q/related   → QRelatedHandler
func MakeQDispatcher(
	c *Context,
	entityProvider func(name string) (config.Entity, bool),
	dataSourceForSchema any, // datasource.DataSource или nil (fallback legacy)
	schemaForLLM SchemaForLLMCallback,
) map[string]http.HandlerFunc {
	return map[string]http.HandlerFunc{
		"/q/map": QMapHandler(schemaForLLM),
		"/q/describe": QDescribeHandler(c, func(n string) bool {
			_, ok := entityProvider(n)
			return ok
		}, func(entityName string) http.HandlerFunc {
			// SchemaStrategy через provider (или legacy).
			entity, ok := entityProvider(entityName)
			if !ok {
				return notFoundHandler()
			}
			idCol := entity.IDColumnOrDefault()
			nameCol := entity.FirstStringFieldColumn()
			strategy := search.NewSchemaStrategy(idCol, nameCol)
			return NewStrategySchemaHandler(c, strategy, entity).ServeHTTP
		}),
		"/q/search": QSearchHandler(c, func(n string) bool {
			_, ok := entityProvider(n)
			return ok
		}, func(entityName string) http.HandlerFunc {
			entity, ok := entityProvider(entityName)
			if !ok {
				return notFoundHandler()
			}
			idCol := entity.IDColumnOrDefault()
			nameCol := entity.FirstStringFieldColumn()
			// searchableRules из entity — для простоты используем default;
			// кастомные пробросятся через колбэк при желании.
			strategy := search.NewGrepStrategy(idCol, nameCol)
			return NewStrategyHandler(c, strategy, entityName, entity)
		}),
		"/q/filter": QFilterHandler(c, func(n string) bool {
			_, ok := entityProvider(n)
			return ok
		}, func(entityName string) http.HandlerFunc {
			entity, ok := entityProvider(entityName)
			if !ok {
				return notFoundHandler()
			}
			idCol := entity.IDColumnOrDefault()
			nameCol := entity.FirstStringFieldColumn()
			strategy := search.NewFilterStrategy(idCol, nameCol)
			return NewStrategyHandler(c, strategy, entityName, entity)
		}),
		"/q/get": QGetHandler(c, func(n string) bool {
			_, ok := entityProvider(n)
			return ok
		}, func(entityName string) http.HandlerFunc {
			if _, ok := entityProvider(entityName); !ok {
				return notFoundHandler()
			}
			// GetByIDHandler читает id через c.URLParam (path-param). Для /q/get
			// id приходит query-параметром — подменяем URLParam на чтение query.
			// Сохраняем оригинал и восстанавливаем после запроса (хендлер может
			// быть переиспользован для других /q/* путей).
			base := GetByIDHandler(c, entityName)
			origURLParam := c.URLParam
			return func(w http.ResponseWriter, r *http.Request) {
				c.URLParam = func(r *http.Request, name string) string {
					if name == "id" {
						return r.URL.Query().Get("id")
					}
					if origURLParam != nil {
						return origURLParam(r, name)
					}
					return ""
				}
				defer func() { c.URLParam = origURLParam }()
				base(w, r)
			}
		}),
		"/q/related": QRelatedHandler(c, func(n string) bool {
			_, ok := entityProvider(n)
			return ok
		}, func(entityName string) http.HandlerFunc {
			entity, ok := entityProvider(entityName)
			if !ok {
				return notFoundHandler()
			}
			handler := NewRelatedHandler(c, entity)
			return handler.ServeHTTP
		}),
	}
}

// notFoundHandler — заглушка 404 (entity не найден).
func notFoundHandler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		RespondError(w, http.StatusNotFound, "unknown_entity",
			"unknown entity. Call db_map to see available entities.")
	}
}

var _ = slog.Debug // keep import if unused later
