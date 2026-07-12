package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"

	"github.com/xeipuuv/gojsonschema"
)

// Validate проверяет rawJSON на соответствие JSON Schema по указанному пути.
//
//   - rawJSON: содержимое config.json ПОСЛЕ envsubst (до json.Unmarshal).
//   - schemaPath: путь к config.schema.json.
//
// Возвращает первую найденную ошибку валидации, обёрнутую в контекст
// "<path>: <reason>". Если schemaPath не существует — возвращает
// ErrSchemaNotFound с обёрткой.
//
// Использует github.com/xeipuuv/gojsonschema (поддерживает Draft 2020-12).
//
// Перед валидацией делает дополнительный sanity-check: rawJSON должен быть
// валидным JSON (это покрывает и синтаксические ошибки, и ошибки parse до
// того, как gojsonschema получит шанс ругаться невнятно).
func Validate(rawJSON []byte, schemaPath string) error {
	// 1. schema должен существовать.
	if _, err := os.Stat(schemaPath); err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return fmt.Errorf("%w: %s", ErrSchemaNotFound, schemaPath)
		}
		return fmt.Errorf("config: stat schema %q: %w", schemaPath, err)
	}

	// 2. rawJSON должен быть валидным JSON.
	var probe any
	if err := json.Unmarshal(rawJSON, &probe); err != nil {
		return fmt.Errorf("config: invalid JSON: %w", err)
	}

	// 3. Загружаем схему.
	schemaLoader := gojsonschema.NewReferenceLoader("file://" + filepath.ToSlash(schemaPath))
	schema, err := gojsonschema.NewSchema(schemaLoader)
	if err != nil {
		return fmt.Errorf("config: load schema %q: %w", schemaPath, err)
	}

	// 4. Валидируем.
	documentLoader := gojsonschema.NewBytesLoader(rawJSON)
	result, err := schema.Validate(documentLoader)
	if err != nil {
		return fmt.Errorf("config: validate: %w", err)
	}

	if !result.Valid() {
		// Возвращаем первую ошибку — её обычно достаточно для дебага.
		// Остальные можно посмотреть через дополнительный helper (не требуется фазой 3.2.a).
		if len(result.Errors()) > 0 {
			e := result.Errors()[0]
			return fmt.Errorf("%s: %s", e.Field(), e.Description())
		}
		return errors.New("config: validation failed (no details)")
	}

	return nil
}
