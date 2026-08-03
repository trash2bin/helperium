package runtime

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"math"
	"strconv"
	"strings"
	"time"
)

// MapRow сканирует одну строку *sql.Rows в map[string]any с публичными
// именами полей сущности и type coercion по entity.Fields.
//
// Сканирует напрямую в нативные Go-типы (int64, float64, bool, string) вместо
// sql.RawBytes, что устраняет лишнюю аллокацию на RawBytes→string и даёт
// правильные типы в JSON ({"id": 123} вместо {"id": "123"}).
func (b *Builder) MapRow(rows *sql.Rows, entity Entity) (map[string]any, error) {
	columns, err := rows.Columns()
	if err != nil {
		return nil, fmt.Errorf("runtime: MapRow: read columns: %w", err)
	}

	dest := make([]any, len(columns))
	for i := range dest {
		dest[i] = new(any)
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

		ptr := dest[i].(*any)
		if ptr == nil || *ptr == nil {
			result[publicName] = nil
			continue
		}

		val := *ptr

		// Type coercion по конфигу поля
		ft := b.fieldTypeFor(entity, publicName)
		result[publicName] = coerceNative(val, ft)
	}
	return result, nil
}

// MapCustomQueryRow сканирует одну строку *sql.Rows в map[string]any
// для custom_query. При наличии маппинга сканирует в нативные Go-типы
// и приводит по ResultMappingField.Type. Без маппинга возвращает строки
// (как legacy-поведение).
func (b *Builder) MapCustomQueryRow(rows *sql.Rows, mapping map[string]ResultMappingField) (map[string]any, error) {
	columns, err := rows.Columns()
	if err != nil {
		return nil, fmt.Errorf("runtime: MapCustomQueryRow: read columns: %w", err)
	}

	dest := make([]any, len(columns))
	for i := range dest {
		dest[i] = new(any)
	}

	if err := rows.Scan(dest...); err != nil {
		return nil, fmt.Errorf("runtime: MapCustomQueryRow: scan: %w", err)
	}

	result := make(map[string]any, len(columns))
	for i, col := range columns {
		ptr := dest[i].(*any)
		if ptr == nil || *ptr == nil {
			result[col] = nil
			continue
		}

		val := *ptr

		// Type coercion по маппингу custom_query
		if mf, ok := mapping[col]; ok {
			result[col] = coerceNative(val, string(mf.Type))
		} else {
			// Без маппинга — legacy поведение: строки
			result[col] = fmt.Sprintf("%v", val)
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
			// early close: release connection back to pool immediately
			_ = rows.Close()
			break
		}
	}
	if err := rows.Err(); err != nil && err != io.EOF {
		return out, fmt.Errorf("runtime: MapRows: iterate: %w", err)
	}
	return out, nil
}

// safeFloatToInt64 безопасно приводит float64 к int64: дробная часть и
// выход за диапазон int64 НЕ замалчиваются — возвращаем (0, false).
// int64(95.7)→95 и int64(1e300)→saturate были тихими искажениями данных.
func safeFloatToInt64(v float64) (int64, bool) { // Диапазон: float64(math.MaxInt64) == 2^63 (округление), поэтому верхняя
	// граница — float64(math.MaxInt64) (2^63) уже вне int64.
	if v < math.MinInt64 || v >= float64(math.MaxInt64) {
		return 0, false
	}
	if math.Trunc(v) != v {
		return 0, false
	}
	return int64(v), true
}

// normalizeDateTime приводит строку даты/времени к RFC3339.
// Поддерживаемые форматы: RFC3339 (уже канонический), sqlite-стиль
// "2006-01-02 15:04:05", date "2006-01-02". Неизвестный формат — ok=false.
func normalizeDateTime(s string) (string, bool) {
	trimmed := strings.TrimSpace(s)
	if trimmed == "" {
		return "", false
	}
	// Layout'ы с таймзоной идут ПЕРВЫМИ (иначе "+00:00" останется хвостом,
	// а "15:04:05.123+00:00" не распарсится без таймзоны в layout).
	for _, layout := range []string{
		time.RFC3339,
		"2006-01-02 15:04:05.999999999Z07:00",
		"2006-01-02T15:04:05.999999999Z07:00",
		"2006-01-02 15:04:05Z07:00",
		"2006-01-02T15:04:05Z07:00",
		"2006-01-02 15:04:05.999999999",
		"2006-01-02T15:04:05.999999999",
		"2006-01-02 15:04:05",
		"2006-01-02T15:04:05",
		"2006-01-02",
	} {
		if t, err := time.Parse(layout, trimmed); err == nil {
			return t.UTC().Format(time.RFC3339), true
		}
	}
	return "", false
}

// CoerceNative — экспортированная обёртка над coerceNative для использования
// в пакете handlers (distinct/schema-хендлеры приводят значения колонок
// к типам из конфига, а не отдают строки).
func CoerceNative(val any, typ string) any {
	return coerceNative(val, typ)
}

// coerceNative приводит нативное значение (int64, float64, bool, string)
// к ожидаемому типу из конфига. Если значение уже правильного типа —
// возвращает как есть. Это позволяет JSON-маршаллеру сериализовать
// числа как числа, а не строки.
func coerceNative(val any, typ string) any {
	if val == nil {
		return nil
	}

	switch typ {
	case "int":
		switch v := val.(type) {
		case int64:
			return v
		case float64:
			if n, ok := safeFloatToInt64(v); ok {
				return n
			}
			// Дробь или out-of-range: не кастуем молча — возвращаем float64.
			slog.Warn("coerce: float value cannot be safely cast to int, keeping float64",
				"value", v)
			return v
		case string:
			if n, err := strconv.ParseInt(v, 10, 64); err == nil {
				return n
			}
		}
		return val

	case "float":
		switch v := val.(type) {
		case float64:
			return v
		case int64:
			return float64(v)
		case string:
			if f, err := strconv.ParseFloat(v, 64); err == nil {
				return f
			}
		}
		return val

	case "bool":
		switch v := val.(type) {
		case bool:
			return v
		case int64:
			return v != 0
		case float64:
			return v != 0
		case string:
			if b, err := strconv.ParseBool(v); err == nil {
				return b
			}
		}
		return val

	case "json":
		switch v := val.(type) {
		case string:
			var js any
			if err := json.Unmarshal([]byte(v), &js); err == nil {
				return js
			}
			return v
		case []byte:
			var js any
			if err := json.Unmarshal(v, &js); err == nil {
				return js
			}
			return string(v)
		}
		return val

	case "datetime", "date":
		// Канонический RFC3339: драйверы отдают по-разному (sqlite — string
		// "2006-01-02 15:04:05", pgx — time.Time). Нормализуем к единому формату.
		switch v := val.(type) {
		case time.Time:
			return v.UTC().Format(time.RFC3339)
		case string:
			if norm, ok := normalizeDateTime(v); ok {
				return norm
			}
			return v
		case []byte:
			if norm, ok := normalizeDateTime(string(v)); ok {
				return norm
			}
			return v
		}
		return val

	default:
		// string, datetime, date, unknown → конвертируем в строку
		switch v := val.(type) {
		case string:
			return v
		case fmt.Stringer:
			return v.String()
		default:
			return fmt.Sprintf("%v", v)
		}
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
