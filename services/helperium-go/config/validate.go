package config

import (
	"encoding/json"
	"fmt"
)

// Validate проверяет rawJSON на валидность JSON, парсит его в Config
// и запускает Config.Validate() для семантической проверки.
//
// Это высокоуровневая обёртка, удобная для admin API, где конфиг
// приходит как сырой JSON (а не из файла).
//
// Для загрузки из файла используйте Load() — он делает то же самое
// плюс envsubst.
func Validate(rawJSON []byte) error {
	// 1. rawJSON должен быть валидным JSON.
	var cfg Config
	if err := json.Unmarshal(rawJSON, &cfg); err != nil {
		return fmt.Errorf("config: invalid JSON: %w", err)
	}

	// 2. Семантическая валидация.
	return cfg.Validate()
}
