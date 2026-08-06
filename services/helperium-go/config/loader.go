package config

import (
	"encoding/json"
	"fmt"
	"os"
)

// Load читает config.json по указанному пути, делает envsubst,
// парсит в *Config и валидирует через Config.Validate().
//
// Конвейер:
//  1. os.ReadFile(path) — raw bytes.
//  2. Envsubst(raw, os.LookupEnv) — подстановка ${ENV} / ${ENV:-default}.
//  3. json.Unmarshal(envsubsted, &cfg) — типизированный парсинг.
//  4. cfg.Normalize() — приведение к актуальной версии схемы.
//  5. cfg.Validate() — семантическая валидация на Go.
//
// Валидация больше не требует внешнего файла config.schema.json —
// enum'ы и cross-entity проверки живут в Go-типах.
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

	// 3. Parse into typed struct.
	var cfg Config
	if err := json.Unmarshal([]byte(substituted), &cfg); err != nil {
		return nil, fmt.Errorf("config: load %q: parse: %w", path, err)
	}

	// 4. Normalize to current schema version (handles v0, v1 → current).
	cfg.Normalize()

	// 5. Semantic validation via Go types.
	if err := cfg.Validate(); err != nil {
		return nil, fmt.Errorf("config: load %q: %w", path, err)
	}

	return &cfg, nil
}
