package runtime

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"strconv"
)

// MapRow сканирует одну строку *sql.Rows в map[string]any с публичными
// именами полей сущности и type coercion по entity.Fields.
func (b *Builder) MapRow(rows *sql.Rows, entity Entity) (map[string]any, error) {
	columns, err := rows.Columns()
	if err != nil {
		return nil, fmt.Errorf("runtime: MapRow: read columns: %w", err)
	}

	dest := make([]any, len(columns))
	for i := range dest {
		var rb sql.RawBytes
		dest[i] = &rb
	}

	if err := rows.Scan(dest...); err != nil {
		return nil, fmt.Errorf("runtime: MapRow: scan: %w", err)
	}

	result := make(map[string]any, len(columns))
	for i, col := range columns {
		var publicName string
		if name, ok := b.publicFor(entity, col); ok {
			publicName = name
		} else {
			continue // неизвестная колонка — пропускаем
		}

		rbPtr, ok := dest[i].(*sql.RawBytes)
		if !ok || rbPtr == nil || *rbPtr == nil {
			result[publicName] = nil
			continue
		}

		val := string(*rbPtr)

		// Type coercion по конфигу поля
		ft := b.fieldTypeFor(entity, publicName)
		result[publicName] = coerceValue(val, ft)
	}
	return result, nil
}

// MapCustomQueryRow сканирует одну строку *sql.Rows в map[string]any
// для custom_query. Всегда возвращает строки (type coercion через mapping
// — задача фазы 3.5+).
func (b *Builder) MapCustomQueryRow(rows *sql.Rows, mapping map[string]ResultMappingField) (map[string]any, error) {
	columns, err := rows.Columns()
	if err != nil {
		return nil, fmt.Errorf("runtime: MapCustomQueryRow: read columns: %w", err)
	}

	dest := make([]any, len(columns))
	for i := range dest {
		var rb sql.RawBytes
		dest[i] = &rb
	}

	if err := rows.Scan(dest...); err != nil {
		return nil, fmt.Errorf("runtime: MapCustomQueryRow: scan: %w", err)
	}

	result := make(map[string]any, len(columns))
	for i, col := range columns {
		rbPtr, ok := dest[i].(*sql.RawBytes)
		if !ok || rbPtr == nil || *rbPtr == nil {
			result[col] = nil
			continue
		}

		val := string(*rbPtr)

		// Type coercion по маппингу custom_query
		if mf, ok := mapping[col]; ok {
			result[col] = coerceValue(val, string(mf.Type))
		} else {
			result[col] = val
		}
	}
	return result, nil
}

// MapRows итерирует rows и вызывает mapper для каждой строки.
func (b *Builder) MapRows(
	rows *sql.Rows,
	mapper func(*sql.Rows) (map[string]any, error),
	maxRows int,
) ([]map[string]any, error) {
	defer func() {
		_ = rows.Close()
	}()

	out := make([]map[string]any, 0)
	count := 0
	for rows.Next() {
		row, err := mapper(rows)
		if err != nil {
			return out, err
		}
		out = append(out, row)
		count++
		if maxRows > 0 && count >= maxRows {
			_ = rows.Close()
			break
		}
	}
	if err := rows.Err(); err != nil && err != io.EOF {
		return out, fmt.Errorf("runtime: MapRows: iterate: %w", err)
	}
	return out, nil
}

// coerceValue приводит строковое значение к типу из конфига.
func coerceValue(val, typ string) any {
	if val == "" {
		return val
	}
	switch typ {
	case "int":
		if n, err := strconv.Atoi(val); err == nil {
			return n
		}
		return val
	case "float":
		if f, err := strconv.ParseFloat(val, 64); err == nil {
			return f
		}
		return val
	case "bool":
		if b, err := strconv.ParseBool(val); err == nil {
			return b
		}
		return val
	case "json":
		var js any
		if err := json.Unmarshal([]byte(val), &js); err == nil {
			return js
		}
		return val
	default:
		return val
	}
}

// publicFor — поиск публичного имени по имени колонки.
func (b *Builder) publicFor(entity Entity, column string) (string, bool) {
	for _, f := range entity.Fields {
		if f.Column == column {
			return f.Name, true
		}
	}
	return "", false
}

// fieldTypeFor — поиск типа поля по публичному имени.
func (b *Builder) fieldTypeFor(entity Entity, publicName string) string {
	for _, f := range entity.Fields {
		if f.Name == publicName {
			return f.Type
		}
	}
	return ""
}
