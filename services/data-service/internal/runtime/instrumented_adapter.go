package runtime

import (
	"context"
	"database/sql"
	"log/slog"
	"time"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/helperium-go/pkg/metrics"
)

// InstrumentedAdapter wraps datasource.Conn + datasource.Adapter into AdapterSubset,
// adding debug logging and Prometheus DB query duration metrics.
type InstrumentedAdapter struct {
	Conn datasource.Conn
	Adp  datasource.Adapter

	// TenantIDFunc extracts tenant ID from context for metrics labels.
	// If nil, the "tenant" Prometheus label defaults to "default".
	TenantIDFunc func(ctx context.Context) string
}

func (a *InstrumentedAdapter) QueryContext(ctx context.Context, query string, args ...any) (*sql.Rows, error) {
	start := time.Now()

	if slog.Default().Enabled(ctx, slog.LevelDebug) {
		slog.Debug("DB Query", "sql", query, "args", args)
	}

	rows, err := a.Conn.QueryContext(ctx, query, args...)

	elapsed := time.Since(start).Seconds() * 1000
	tenantID := "default"
	if a.TenantIDFunc != nil {
		tenantID = a.TenantIDFunc(ctx)
	}
	metrics.DBQueryDuration.WithLabelValues(tenantID).Observe(elapsed)

	if err != nil {
		slog.Warn("DB Error", "error", err, "sql", query)
	}

	return rows, err
}

func (a *InstrumentedAdapter) PingContext(ctx context.Context) error {
	return a.Conn.PingContext(ctx)
}

func (a *InstrumentedAdapter) QuoteIdentifier(name string) string {
	return a.Adp.QuoteIdentifier(name)
}

func (a *InstrumentedAdapter) TranslatePlaceholder(index int) string {
	return a.Adp.TranslatePlaceholder(index)
}
