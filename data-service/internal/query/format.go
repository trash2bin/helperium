package query

// SearchResult — универсальный ответ для всех search endpoint'ов.
type SearchResult struct {
	Total    int           `json:"total"`
	Returned int           `json:"returned"`
	Preview  []CompactRow  `json:"preview,omitempty"`
	Data     []map[string]any `json:"data,omitempty"`
}

// CompactRow — краткое представление строки (id + name).
type CompactRow struct {
	ID   any    `json:"id"`
	Name string `json:"name"`
}

// FormatRows форматирует строки результата в SearchResult.
//
// Параметры:
//   - rows — исходные строки (ключи — имена колонок БД).
//   - total — общее количество строк (до пагинации).
//   - format — формат ответа.
//   - entityIDCol — имя ID-колонки в rows.
//   - entityNameCol — имя name-колонки в rows.
//
// Для FormatCompact: выбирает ID из entityIDCol и первое строковое поле как Name.
// Для FormatFull: все колонки как есть.
// Для FormatCount: только Total (Returned=0, Data/Preview пусты).
func FormatRows(rows []map[string]any, total int, format ResponseFormat, entityIDCol, entityNameCol string) SearchResult {
	switch format {
	case FormatCount:
		return SearchResult{
			Total:    total,
			Returned: 0,
		}

	case FormatCompact:
		preview := make([]CompactRow, 0, len(rows))
		for _, row := range rows {
			cr := CompactRow{
				ID:   row[entityIDCol],
				Name: firstStringField(row, entityNameCol),
			}
			preview = append(preview, cr)
		}
		return SearchResult{
			Total:    total,
			Returned: len(rows),
			Preview:  preview,
		}

	default: // FormatFull
		return SearchResult{
			Total:    total,
			Returned: len(rows),
			Data:     rows,
		}
	}
}

// firstStringField возвращает первое строковое значение из row.
// Если entityNameCol задан и непуст — приоритет ему.
func firstStringField(row map[string]any, nameCol string) string {
	// Сначала проверяем указанную name-колонку.
	if nameCol != "" {
		if v, ok := row[nameCol]; ok {
			if s, ok := v.(string); ok {
				return s
			}
		}
	}

	// Fallback: первое попавшееся строковое поле.
	for _, v := range row {
		if s, ok := v.(string); ok {
			return s
		}
	}
	return ""
}
