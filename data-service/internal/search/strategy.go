package search

import (
	"net/http"

	"github.com/trash2bin/helperium/data-service/internal/query"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// Strategy — стратегия парсинга HTTP/MCP-запроса в QueryPlan.
//
// Каждая стратегия знает:
//   - как разобрать HTTP-запрос в QueryPlan
//   - как сгенерировать MCP tool definition (имя, описание, параметры)
//   - какие колонки сущности использовать для компактного формата
type Strategy interface {
	// Name — уникальное имя стратегии ("grep", "filter", "schema").
	Name() string

	// ParseRequest разбирает HTTP-запрос и entity в QueryPlan.
	// entity — уже разрешённая сущность из Resolver.
	// a — адаптер для квотирования и placeholder'ов.
	ParseRequest(r *http.Request, entity config.Entity, a Adapter) (*query.QueryPlan, error)

	// ToolName — имя MCP инструмента (grep_products, filter_orders).
	ToolName(entity config.Entity) string

	// ToolDescription — LLM-friendly описание инструмента.
	ToolDescription(entity config.Entity) string

	// ToolParams — MCP-параметры для config.MCPTool.Params.
	ToolParams(entity config.Entity) []config.EndpointParam

	// EntityIDCol — имя ID-колонки (для compact format).
	EntityIDCol() string

	// EntityNameCol — имя name-колонки (для compact format).
	EntityNameCol() string
}

// Adapter — минимальный интерфейс адаптера, необходимый стратегиям поиска.
//
// Покрывает квотирование идентификаторов, экранирование LIKE-символов
// и генерацию placeholder'ов. Реализуется обёрткой над query.AdapterSubset
// или напрямую тестовым стабом.
type Adapter interface {
	// QuoteIdentifier квотирует имя таблицы/колонки.
	QuoteIdentifier(name string) string

	// QuoteString экранирует '%' и '_' в LIKE-паттерне.
	QuoteString(s string) string

	// TranslatePlaceholder возвращает нативный placeholder для индекса.
	TranslatePlaceholder(index int) string

	// IsPostgres — true если адаптер использует PostgreSQL-стиль ($N).
	IsPostgres() bool
}

// adapterWrapper оборачивает query.AdapterSubset в search.Adapter.
type adapterWrapper struct {
	inner      query.AdapterSubset
	isPostgres bool
}

// NewAdapter создаёт search.Adapter из query.AdapterSubset.
func NewAdapter(inner query.AdapterSubset) Adapter {
	pg := inner.TranslatePlaceholder(1) != "?"
	return &adapterWrapper{inner: inner, isPostgres: pg}
}

func (w *adapterWrapper) QuoteIdentifier(name string) string     { return w.inner.QuoteIdentifier(name) }
func (w *adapterWrapper) QuoteString(s string) string            { return w.inner.QuoteString(s) }
func (w *adapterWrapper) TranslatePlaceholder(index int) string  { return w.inner.TranslatePlaceholder(index) }
func (w *adapterWrapper) IsPostgres() bool                        { return w.isPostgres }
