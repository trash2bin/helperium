package runtime

import "strings"

// Builder — собирает SELECT-запросы по конфигу Entity/CustomQuery
// с использованием адаптера для placeholder'ов и квотирования.
//
// Builder не делает сетевых вызовов — только строит Query.
// Выполнение запроса — задача вызывающего кода (обычно handler).
//
// Все методы безопасны для конкурентного использования, поскольку
// Builder не хранит состояние между вызовами.
type Builder struct {
	// adapter — урезанный интерфейс (см. AdapterSubset в types.go).
	// Это разрывает цикл импортов runtime → datasource.
	adapter AdapterSubset
}

// NewBuilder создаёт Builder поверх адаптера.
//
// Адаптер должен быть уже подключён к БД (если Builder используется
// только для сборки строк — подключение не нужно, но и не вредит).
func NewBuilder(adapter AdapterSubset) *Builder {
	return &Builder{adapter: adapter}
}

// BuildGetByID собирает SELECT всех колонок сущности с фильтром по PK.
//
// Пример результата для customers (id, email, created_at):
//
//	SELECT "id", "email", "created_at" FROM "customers" WHERE "id" = ?
//
// Args: [idValue]. Caller передаёт значение PK как any (часто int64 или string).
func (b *Builder) BuildGetByID(entity Entity, idValue any) (Query, error) {
	if entity.Table == "" {
		return Query{}, &QueryError{
			Op:     "BuildGetByID",
			Reason: "entity has empty Table",
		}
	}
	if entity.IDColumn == "" {
		return Query{}, &QueryError{
			Op:     "BuildGetByID",
			Reason: "entity has empty IDColumn",
		}
	}

	cols := buildColumnList(b.adapter, entity)
	ph := b.adapter.TranslatePlaceholder(1)

	// Прямое квотирование PK-колонки — допустимо: имя приходит из конфига,
	// не из user input. В SQL это часть статической структуры запроса.
	q := Query{
		SQL:  `SELECT ` + cols + ` FROM ` + b.adapter.QuoteIdentifier(entity.Table) + ` WHERE ` + b.adapter.QuoteIdentifier(entity.IDColumn) + ` = ` + ph,
		Args: []any{idValue},
	}
	return q, nil
}

// BuildFind собирает SELECT с поиском по одному публичному полю.
//
// Поле ищется в entity.Fields по публичному имени (entity.Fields[].Name),
// в SQL подставляется реальное имя колонки. Если поле не найдено —
// ошибка QueryError.
//
// Пример: searchField="email", value="x@y.com" →
//
//	SELECT "id", "email", "created_at" FROM "customers" WHERE "email" = ?
//
// Args: [value]. Caller экранирует значение через prepared statement.
func (b *Builder) BuildFind(entity Entity, searchField, value string) (Query, error) {
	if entity.Table == "" {
		return Query{}, &QueryError{
			Op:     "BuildFind",
			Reason: "entity has empty Table",
		}
	}

	column, ok := b.columnFor(entity, searchField)
	if !ok {
		return Query{}, &QueryError{
			Op:     "BuildFind",
			Reason: "unknown search field " + quote(searchField) + " for entity " + quote(entity.Name),
		}
	}

	cols := buildColumnList(b.adapter, entity)
	ph := b.adapter.TranslatePlaceholder(1)

	// LIKE-поиск: совместимость со старыми хендлерами (поиск по подстроке).
	// Безопасность: value в prepared statement, wildcards %/_ интерпретируются LIKE —
	// это желаемое поведение для поиска.
	searchVal := "%" + value + "%"

	q := Query{
		SQL:  `SELECT ` + cols + ` FROM ` + b.adapter.QuoteIdentifier(entity.Table) + ` WHERE ` + b.adapter.QuoteIdentifier(column) + ` LIKE ` + ph,
		Args: []any{searchVal},
	}
	return q, nil
}

// BuildList собирает SELECT всех колонок сущности.
//
// whereClause — сырая строка фильтра ("status = ? AND tenant_id = ?"),
// caller отвечает за её корректность и за порядок args. builder не парсит
// whereClause — только конкатенирует.
//
// Если whereClause пуст — генерируется чистый SELECT без WHERE.
//
// Параметры:
//   - whereClause — пусто или "status = ? AND ..." (с '?' placeholder'ами)
//   - args        — значения для placeholder'ов в том же порядке
//
// Пример без where:
//
//	SELECT "id", "email" FROM "customers"
//
// Пример с where:
//
//	SELECT "id", "email" FROM "customers" WHERE status = ? AND tenant_id = ?
func (b *Builder) BuildList(entity Entity, whereClause string, args []any) (Query, error) {
	if entity.Table == "" {
		return Query{}, &QueryError{
			Op:     "BuildList",
			Reason: "entity has empty Table",
		}
	}

	cols := buildColumnList(b.adapter, entity)
	sql := `SELECT ` + cols + ` FROM ` + b.adapter.QuoteIdentifier(entity.Table)

	if w := strings.TrimSpace(whereClause); w != "" {
		sql += ` WHERE ` + w
	}

	return Query{SQL: sql, Args: args}, nil
}

// BuildCustomQuery собираёт запрос из CustomQuery с prepared args.
//
// Подставляет нативные placeholder'ы вместо generic '? в cq.SQL.
// Если длина args не совпадает с длиной cq.Params — возвращает ошибку
// QueryError. Это защита от рассинхрона caller-side.
//
// Валидация: cq.SQL ДОЛЖЕН начинаться с SELECT (case-insensitive) —
// это whitelist-защита от destructive SQL в custom_query админом.
func (b *Builder) BuildCustomQuery(cq CustomQuery, args []any) (Query, error) {
	if !looksLikeSelect(cq.SQL) {
		return Query{}, &QueryError{
			Op:     "BuildCustomQuery",
			Reason: "custom query must be a SELECT statement, got: " + summarizeSQL(cq.SQL),
		}
	}
	if len(args) != len(cq.Params) {
		return Query{}, &QueryError{
			Op:     "BuildCustomQuery",
			Reason: paramCountMismatchReason(len(cq.Params), len(args)),
		}
	}

	out := cq.SQL
	// Заменяем '?' слева направо по индексу параметра. Мы не можем
	// использовать strings.Replace — он заменяет все вхождения сразу,
	// а нам нужно ставить $1, $2, ... в порядке появления в SQL.
	for i := 1; i <= len(cq.Params); i++ {
		idx := strings.Index(out, "?")
		if idx < 0 {
			// Меньше placeholder'ов, чем параметров — это программная
			// ошибка cq.SQL, защищаемся явно.
			return Query{}, &QueryError{
				Op:     "BuildCustomQuery",
				Reason: "placeholder count mismatch in SQL: fewer '?' than Params",
			}
		}
		out = out[:idx] + b.adapter.TranslatePlaceholder(i) + out[idx+1:]
	}

	return Query{SQL: out, Args: args}, nil
}

// buildColumnList — внутренний helper: список квотированных колонок через запятую.
//
// Если Fields пуст — возвращается "*", чтобы SQL-запрос оставался валидным.
func buildColumnList(adapter AdapterSubset, entity Entity) string {
	if len(entity.Fields) == 0 {
		return "*"
	}
	// entity.Fields содержит публичные имена; в SELECT подставляются
	// реальные колонки БД (Column) — это часть маппинга, который
	// builder обязан выполнять. Имена колонок квотируются через
	// адаптер для консистентности с FROM/WHERE и для имён с пробелами.
	parts := make([]string, 0, len(entity.Fields))
	for _, f := range entity.Fields {
		parts = append(parts, adapter.QuoteIdentifier(f.Column))
	}
	return strings.Join(parts, ", ")
}

// columnFor — поиск имени колонки по публичному имени поля.
// Инкапсулирован в Builder, чтобы не зависеть от EntityResolver
// (builder принимает Entity напрямую).
func (b *Builder) columnFor(entity Entity, publicField string) (string, bool) {
	for _, f := range entity.Fields {
		if f.Name == publicField {
			return f.Column, true
		}
	}
	return "", false
}

// paramCountMismatchReason — человеко-читаемое сообщение об ошибке
// валидации числа аргументов для custom_query.
func paramCountMismatchReason(expected, got int) string {
	return "arg count mismatch: query expects " +
		itoa(expected) + " params, got " + itoa(got)
}

// itoa — локальный strconv.Itoa, чтобы не тянуть strconv в hot-path.
// Используется только в сообщениях об ошибках.
func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := false
	if n < 0 {
		neg = true
		n = -n
	}
	var buf [20]byte
	i := len(buf)
	for n > 0 {
		i--
		buf[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		buf[i] = '-'
	}
	return string(buf[i:])
}

// looksLikeSelect — проверяет что SQL начинается с SELECT (case-insensitive).
// Пропускает начальные пробелы и переводы строк.
func looksLikeSelect(sql string) bool {
	trimmed := strings.TrimSpace(sql)
	if len(trimmed) < 6 {
		return false
	}
	prefix := strings.ToLower(trimmed[:6])
	return prefix == "select"
}

// summarizeSQL — обрезает SQL до первых N символов для сообщения об ошибке.
func summarizeSQL(sql string) string {
	const maxLen = 60
	s := strings.TrimSpace(sql)
	if len(s) > maxLen {
		s = s[:maxLen] + "..."
	}
	return `"` + s + `"`
}

// quote — локальный strconv.Quote для сообщений об ошибках.
func quote(s string) string {
	return `"` + s + `"`
}
