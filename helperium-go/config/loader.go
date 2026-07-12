package config

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

// Load читает config.json по указанному пути, делает envsubst,
// парсит в *Config и валидирует по JSON Schema.
//
// Конвейер:
//  1. os.ReadFile(path) — raw bytes.
//  2. Envsubst(raw, os.LookupEnv) — подстановка ${ENV} / ${ENV:-default}.
//  3. Validate(envsubsted, schemaPath) — JSON Schema 2020-12.
//  4. json.Unmarshal(envsubsted, &cfg) — типизированный парсинг.
//
// Поиск schemaPath (первый существующий побеждает):
//  1. Переменная окружения CONFIG_SCHEMA (если задана).
//  2. "specs/config.schema.json" относительно текущей рабочей директории.
//  3. "../../specs/config.schema.json" относительно os.Executable().
//
// Если схема не найдена ни одним путём — возвращается ErrSchemaNotFound
// с обёрткой про все проверенные пути.
//
// Все ошибки оборачиваются с префиксом "config: load <path>:".
func Load(path string) (*Config, error) {
	// 1. Read.
	raw, err := os.ReadFile(path) //nolint:gosec // config path comes from caller
	if err != nil {
		return nil, fmt.Errorf("config: load %q: %w", path, err)
	}

	// 2. envsubst.
	substituted, err := Envsubst(string(raw), os.LookupEnv)
	if err != nil {
		return nil, fmt.Errorf("config: load %q: %w", path, err)
	}

	// 3. Locate schema.
	schemaPath, err := findSchema()
	if err != nil {
		return nil, fmt.Errorf("config: load %q: %w", path, err)
	}

	// 4. Validate against schema (raw JSON check + JSON Schema validation).
	if err := Validate([]byte(substituted), schemaPath); err != nil {
		return nil, fmt.Errorf("config: load %q: %w", path, err)
	}

	// 5. Parse into typed struct.
	var cfg Config
	if err := json.Unmarshal([]byte(substituted), &cfg); err != nil {
		return nil, fmt.Errorf("config: load %q: parse: %w", path, err)
	}

	return &cfg, nil
}

// findSchema ищет config.schema.json по цепочке путей.
// Возвращает ErrSchemaNotFound если ни один путь не сработал.
func findSchema() (string, error) {
	candidates := []string{}

	if env := os.Getenv("CONFIG_SCHEMA"); env != "" {
		candidates = append(candidates, env)
	}

	if cwd, err := os.Getwd(); err == nil {
		// Относительно CWD: specs/config.schema.json (проект в корне агента)
		candidates = append(candidates, filepath.Join(cwd, "specs", "config.schema.json"))
	}

	if exe, err := os.Executable(); err == nil {
		exeDir := filepath.Dir(exe)
		// Относительно бинарника (data-service/bin/): ../../specs/config.schema.json
		candidates = append(candidates, filepath.Join(exeDir, "..", "..", "specs", "config.schema.json"))
	}

	for _, c := range candidates {
		if _, err := os.Stat(c); err == nil {
			return c, nil
		}
	}

	return "", fmt.Errorf("%w (looked in: %v)", ErrSchemaNotFound, candidates)
}
