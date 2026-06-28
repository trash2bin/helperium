package runtime

import (
	"database/sql"
	"fmt"
	"io"
)

// MapRow сканирует одну строку *sql.Rows в map[string]any с публичными
// именами полей сущности.
//
// Алгоритм:
//  1. Получить список колонок из rows.Columns() (имена колонок БД).
//  2. Подготовить []any destination с достаточной ёмкостью.
//  3. rows.Scan(dest...) — database/sql заполняет destination
//     в соответствии с типами колонок.
//  4. Для каждой колонки найти публичное имя в entity.Fields и
//     положить в map. Колонки, которых нет в entity.Fields,
//     пропускаются (не падаем).
//
// MapRow не вызывает rows.Next() — caller должен позаботиться
// о позиционировании курсора. Типичная схема:
//
//	rows, _ := conn.QueryContext(ctx, query.SQL, query.Args...)
//	defer rows.Close()
//	for rows.Next() {
//	    row, err := builder.MapRow(rows, entity)
//	    ...
//	}
//
// Для NULL-значений database/sql возвращает nil в destination
// после Scan — это совместимо с map[string]any.
func (b *Builder) MapRow(rows *sql.Rows, entity Entity) (map[string]any, error) {
	columns, err := rows.Columns()
	if err != nil {
		return nil, fmt.Errorf("runtime: MapRow: read columns: %w", err)
	}

	dest := make([]any, len(columns))
	for i := range dest {
		// rawBytes накапливает значение в себе; не выделяем string-буферы
		// заранее — Scan сам выберет подходящее представление.
		var rb sql.RawBytes
		dest[i] = &rb
	}

	if err := rows.Scan(dest...); err != nil {
		return nil, fmt.Errorf("runtime: MapRow: scan: %w", err)
	}

	result := make(map[string]any, len(columns))
	for i, col := range columns {
		// Маппинг колонка → публичное имя. Если колонка не описана
		// в entity.Fields — пропускаем. Это защищает от утечки
		// "технических" колонок в API-ответ.
		publicName := col
		if name, ok := b.publicFor(entity, col); ok {
			publicName = name
		} else {
			// Неизвестная колонка — пропускаем молча, чтобы response
			// mapper не падал на расхождениях схемы и конфига.
			continue
		}

		// После Scan RawBytes содержит байты значения (или nil для NULL).
		rbPtr, ok := dest[i].(*sql.RawBytes)
		if !ok || rbPtr == nil {
			// Программная ошибка builder'а — но не паникуем.
			result[publicName] = nil
			continue
		}
		if rbPtr == nil || *rbPtr == nil {
			result[publicName] = nil
			continue
		}
		// Копируем байты в строку — RawBytes валиден только до следующего
		// rows.Next()/rows.Scan(). Caller должен использовать result
		// до следующей итерации.
		result[publicName] = string(*rbPtr)
	}
	return result, nil
}

// MapCustomQueryRow сканирует одну строку *sql.Rows в map[string]any
// для custom_query — без переименования колонок.
//
// Имена колонок берутся как есть (как в SQL запросе). mapping
// используется только для type/nullable-информации (пока —
// фиксируем "string" для всех; приведение типов — задача фазы 3.5+).
//
// Если колонка из SQL отсутствует в mapping — она всё равно попадает
// в результат под именем колонки (для backward-compat с простыми
// custom_queries, у которых mapping может быть неполным).
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
		rbPtr := dest[i].(*sql.RawBytes)
		if rbPtr == nil || *rbPtr == nil {
			result[col] = nil
			continue
		}
		result[col] = string(*rbPtr)
		// mapping пока не используется для type coercion — только как
		// schema-level документация. В фазе 3.5+ добавим приведение
		// типов на основе mapping[col].Type.
		_ = mapping
	}
	return result, nil
}

// MapRows итерирует rows и вызывает mapper для каждой строки.
//
// Особенности:
//   - После maxRows строк вызывается rows.Close() и цикл прерывается.
//     Это защита от OOM, если БД вернула миллионы строк.
//   - rows.Err() проверяется после цикла — ошибки итерации попадают
//     в возвращаемый результат.
//   - При ошибке mapper — строки до ошибки уже накоплены в out,
//     плюс возвращается ошибка. Caller решает, что важнее.
//
// Если maxRows <= 0 — ограничение не применяется (читаем всё).
func (b *Builder) MapRows(
	rows *sql.Rows,
	mapper func(*sql.Rows) (map[string]any, error),
	maxRows int,
) ([]map[string]any, error) {
	defer func() {
		// Закрываем rows в любом случае — иначе соединение не вернётся в пул.
		// Игнорируем ошибку Close — обычно она возникает при уже закрытом rows.
		_ = rows.Close()
	}()

	var out []map[string]any
	count := 0
	for rows.Next() {
		row, err := mapper(rows)
		if err != nil {
			// Возвращаем уже накопленные строки + ошибку.
			// Это даёт caller'у частичный результат для диагностики.
			return out, err
		}
		out = append(out, row)
		count++
		if maxRows > 0 && count >= maxRows {
			// Достигли лимита — закрываем курсор и прерываем.
			_ = rows.Close()
			break
		}
	}
	if err := rows.Err(); err != nil && err != io.EOF {
		return out, fmt.Errorf("runtime: MapRows: iterate: %w", err)
	}
	return out, nil
}

// publicFor — поиск публичного имени по имени колонки.
// Дублирует EntityResolver.PublicFor, чтобы Builder был самодостаточным
// (не требует обязательного EntityResolver).
func (b *Builder) publicFor(entity Entity, column string) (string, bool) {
	for _, f := range entity.Fields {
		if f.Column == column {
			return f.Name, true
		}
	}
	return "", false
}