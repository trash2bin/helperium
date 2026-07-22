// Package search — SchemaStrategy возвращает мета-информацию о сущности.
//
// LLM-facing name: schema_{entity}
// Возвращает: total count, distinct values для каждого поля, min/max/avg для numeric.
// Используется для discovery перед поиском — один запрос вместо distinct_* + count_*.
package search

import (
	"fmt"
	"net/http"
	"strings"

	"github.com/trash2bin/helperium/data-service/internal/query"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// SchemaStrategy — возвращает мета-информацию о сущности: distinct values, min/max, count.
// LLM использует для discovery перед поиском.
type SchemaStrategy struct {
	idCol   string
	nameCol string
}

// NewSchemaStrategy creates a SchemaStrategy.
func NewSchemaStrategy(idCol, nameCol string) *SchemaStrategy {
	return &SchemaStrategy{idCol: idCol, nameCol: nameCol}
}

func (s *SchemaStrategy) Name() string          { return "schema" }
func (s *SchemaStrategy) EntityIDCol() string   { return s.idCol }
func (s *SchemaStrategy) EntityNameCol() string { return s.nameCol }

func (s *SchemaStrategy) ToolName(entity config.Entity) string {
	return "schema_" + entity.Name
}

func (s *SchemaStrategy) ToolDescription(entity config.Entity) string {
	return fmt.Sprintf(
		"Get metadata about %[1]s: total count, available values for each field, "+
			"min/max for numeric fields. Use BEFORE search to discover valid values. "+
			"One lightweight query — cheaper than distinct_* + count_* separately.\n"+
			"\n"+
			"Example: schema_%[1]s() → {total: 35, fields: {brand: [Brembo, Bosch], price: {min: 100, max: 45000}}}",
		entity.Name,
	)
}

func (s *SchemaStrategy) ToolParams(entity config.Entity) []config.EndpointParam {
	return nil // schema не требует параметров — всегда полный ответ
}

// ParseRequest для schema-стратегии возвращает nil QueryPlan и nil ошибку.
// SchemaHandler обрабатывает запрос отдельно, без использования Engine.
func (s *SchemaStrategy) ParseRequest(r *http.Request, entity config.Entity, a Adapter) (*query.QueryPlan, error) {
	// Schema не использует Engine.Build — handler работает напрямую с БД.
	return nil, nil
}

// FieldInfo возвращает информацию о поле для schema-ответа.
func (s *SchemaStrategy) FieldInfo(entity config.Entity) []config.EntityField {
	// Исключаем PK и tenant_id из schema
	var fields []config.EntityField
	for _, f := range entity.Fields {
		if f.PrimaryKey != nil && *f.PrimaryKey {
			continue
		}
		if f.Column == "tenant_id" {
			continue
		}
		fields = append(fields, f)
	}
	return fields
}

// FormatFields возвращает имена полей для LLM-friendly отображения.
func FormatFields(fields []config.EntityField) string {
	var names []string
	for _, f := range fields {
		names = append(names, f.Name)
	}
	return strings.Join(names, ", ")
}
