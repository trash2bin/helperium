// Package config — загрузчик и валидатор config.json для data-service.
//
// Реализует Phase 3.2.a: envsubst + JSON Schema validation + типизированный
// парсинг в Go-структуры. Runtime-использование конфига (query builder,
// endpoint builder) — следующие подфазы 3.2.b+.
package config

import "errors"

// ErrSchemaNotFound возвращается, когда не удалось найти файл
// config.schema.json ни одним из известных путей.
var ErrSchemaNotFound = errors.New("config schema not found")
