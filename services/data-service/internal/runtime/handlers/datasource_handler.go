package handlers

import (
	"log/slog"
	"net/http"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
)

// SchemaHandler — HTTP handler для DataSource-based schema инструмента.
//
// Принимает datasource.DataSource и возвращает мета-информацию о сущности:
// total count, distinct values, min/max для numeric полей.
type SchemaHandler struct {
	ds     datasource.DataSource
	entity string
}

// NewSchemaHandler создаёт SchemaHandler.
func NewSchemaHandler(ds datasource.DataSource, entity string) *SchemaHandler {
	return &SchemaHandler{ds: ds, entity: entity}
}

func (h *SchemaHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	info, err := h.ds.Schema(r.Context(), h.entity)
	if err != nil {
		slog.Error("DataSource schema error", "err", err, "entity", h.entity)
		RespondError(w, http.StatusInternalServerError, "query_failed", "Schema query failed.")
		return
	}
	RespondJSON(w, http.StatusOK, info)
}
