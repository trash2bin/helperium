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
