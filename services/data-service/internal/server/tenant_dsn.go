package server

import (
	"path/filepath"
	"strings"
)

func resolveDataSourceDSN(dsn, configPath string) string {
	if dsn == "" || configPath == "" || strings.Contains(dsn, "://") {
		return dsn
	}

	prefix, path, suffix := "", dsn, ""
	if strings.HasPrefix(path, "file:") {
		prefix, path = "file:", strings.TrimPrefix(path, "file:")
	}
	if i := strings.IndexByte(path, '?'); i >= 0 {
		path, suffix = path[:i], path[i:]
	}
	if path != "" && path != ":memory:" && !filepath.IsAbs(path) {
		path = filepath.Join(filepath.Dir(configPath), path)
	}
	return prefix + path + suffix
}

// sqliteReadOnlyDSN returns an SQLite URI that enforces database-level read-only
// access. The original DSN remains available for admin operations and schema
// introspection; all runtime data queries use the returned URI. In-memory
// databases have no filesystem-backed read-only mode and are left unchanged.
func sqliteReadOnlyDSN(dsn string) string {
	if dsn == "" || dsn == ":memory:" || strings.HasPrefix(dsn, ":memory:?") {
		return dsn
	}
	if strings.Contains(dsn, "mode=ro") || strings.Contains(dsn, "immutable=1") {
		return dsn
	}
	if !strings.HasPrefix(dsn, "file:") {
		dsn = "file:" + dsn
	}
	sep := "?"
	if strings.Contains(dsn, "?") {
		sep = "&"
	}
	return dsn + sep + "mode=ro"
}
