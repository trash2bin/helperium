package query

import (
	"fmt"
	"strings"
)

// AdapterSubset — минимальный интерфейс адаптера для query builder'а.
//
// Расширяет контракт из runtime/types.go:AdapterSubset методом QuoteString,
// необходимым для экранирования LIKE-паттернов.
type AdapterSubset interface {
	// TranslatePlaceholder преобразует порядковый номер placeholder'а
	// в нативный синтаксис СУБД (sqlite → "?", postgres → "$1").
	TranslatePlaceholder(index int) string

	// QuoteIdentifier квотирует имя таблицы/колонки для SQL.
	QuoteIdentifier(name string) string

	// QuoteString экранирует LIKE-специальные символы '%' и '_' в строке.
	// Для SQLite/Postgres: '%' → "\%", '_' → "\_".
	QuoteString(s string) string
}

// Engine — expression-based query builder.
//
// Превращает QueryPlan в SQL+args через Build / BuildCount.
// Потокобезопасен: не хранит состояние между вызовами.
type Engine struct {
	adapter    AdapterSubset
	isPostgres bool // true, если placeholder содержит "$"
}

// NewEngine создаёт Engine с заданным адаптером.
func NewEngine(adapter AdapterSubset) *Engine {
	pg := strings.Contains(adapter.TranslatePlaceholder(1), "$")
	return &Engine{adapter: adapter, isPostgres: pg}
}

// Build собирает SELECT-запрос из QueryPlan.
//
// Возвращает SQL с нативными placeholder'ами и args в том же порядке.
func (e *Engine) Build(plan QueryPlan) (sql string, args []any, err error) {
	return e.build(plan, false)
}

// BuildCount собирает SELECT COUNT(*) вместо колонок, сохраняя WHERE/ORDER/пагинацию.
//
// Используется для /count endpoint'ов или для получения общего числа строк.
func (e *Engine) BuildCount(plan QueryPlan) (sql string, args []any, err error) {
	return e.build(plan, true)
}

func (e *Engine) build(plan QueryPlan, count bool) (sql string, args []any, err error) {
	var b strings.Builder
	phIdx := 1

	// 1. SELECT
	if count {
		b.WriteString("SELECT COUNT(*)")
	} else {
		b.WriteString("SELECT ")
		b.WriteString(e.buildColumnList(plan.Select.Columns))
	}

	// 2. FROM
	if plan.From == "" {
		return "", nil, fmt.Errorf("query: From is empty")
	}
	b.WriteString(" FROM ")
	b.WriteString(plan.From)

	// 3. WHERE
	if plan.RawWhere != "" {
		b.WriteString(" WHERE ")
		b.WriteString(plan.RawWhere)
		args = append(args, plan.RawWhereArgs...)
		phIdx += len(plan.RawWhereArgs)
	} else if len(plan.Where) > 0 {
		conds := make([]string, 0, len(plan.Where))
		for _, c := range plan.Where {
			clause, extraArgs, err := e.renderCondition(c, &phIdx)
			if err != nil {
				return "", nil, fmt.Errorf("query: condition on %q: %w", c.Field, err)
			}
			conds = append(conds, clause)
			args = append(args, extraArgs...)
		}
		b.WriteString(" WHERE ")
		b.WriteString(strings.Join(conds, " AND "))
	}

	// 4. ORDER BY (skip for count)
	if !count && len(plan.Order) > 0 {
		ords := make([]string, 0, len(plan.Order))
		for _, o := range plan.Order {
			if o.Desc {
				ords = append(ords, o.Field+" DESC")
			} else {
				ords = append(ords, o.Field+" ASC")
			}
		}
		b.WriteString(" ORDER BY ")
		b.WriteString(strings.Join(ords, ", "))
	}

	// 5. LIMIT / OFFSET (skip for count)
	if !count && plan.Limit > 0 {
		b.WriteString(" LIMIT ")
		b.WriteString(e.adapter.TranslatePlaceholder(phIdx))
		args = append(args, plan.Limit)
		phIdx++
	}
	if !count && plan.Offset > 0 {
		b.WriteString(" OFFSET ")
		b.WriteString(e.adapter.TranslatePlaceholder(phIdx))
		args = append(args, plan.Offset)
		phIdx++
	}

	return b.String(), args, nil
}

// RenderConditions converts []Condition to a WHERE clause fragment using the
// engine's adapter for placeholder generation and SQL dialect.
// separator is placed between conditions (typically " AND ").
// phIdx must point to the current placeholder index (1-based).
// After return, *phIdx is incremented by the number of placeholders used.
func (e *Engine) RenderConditions(conds []Condition, separator string, phIdx *int) (string, []any, error) {
	if len(conds) == 0 {
		return "", nil, nil
	}
	parts := make([]string, 0, len(conds))
	var args []any
	for _, c := range conds {
		clause, extraArgs, err := e.renderCondition(c, phIdx)
		if err != nil {
			return "", nil, err
		}
		parts = append(parts, clause)
		args = append(args, extraArgs...)
	}
	return strings.Join(parts, " "+separator+" "), args, nil
}

// buildColumnList — строит SELECT-список из колонок или "*".

func (e *Engine) buildColumnList(cols []string) string {
	if len(cols) == 0 {
		return "*"
	}
	return strings.Join(cols, ", ")
}

// renderCondition превращает одно Condition в SQL-фрагмент + args.
func (e *Engine) renderCondition(c Condition, phIdx *int) (string, []any, error) {
	// FieldRef is set only by strategies that validated a second entity field
	// and quoted it through the active adapter. Never treat user input as a
	// raw identifier here.
	if c.FieldRef != "" {
		var op string
		switch c.Operator {
		case OpLt:
			op = "<"
		case OpGt:
			op = ">"
		case OpLte:
			op = "<="
		case OpGte:
			op = ">="
		default:
			return "", nil, fmt.Errorf("field reference requires a comparison operator")
		}
		if c.Not {
			return "", nil, fmt.Errorf("field reference comparison does not support NOT")
		}
		return c.Field + " " + op + " " + c.FieldRef, nil, nil
	}

	switch c.Operator {
	case OpEq:
		ph := e.adapter.TranslatePlaceholder(*phIdx)
		*phIdx++
		// Инверсия равенства — <>, а не невалидный "NOT =".
		op := "="
		if c.Not {
			op = "<>"
		}
		return c.Field + " " + op + " " + ph, []any{c.Value}, nil

	case OpNeq:
		ph := e.adapter.TranslatePlaceholder(*phIdx)
		*phIdx++
		// "NOT !=" — двойное отрицание, семантически "=".
		if c.Not {
			return c.Field + " = " + ph, []any{c.Value}, nil
		}
		return c.Field + " != " + ph, []any{c.Value}, nil

	case OpLt:
		ph := e.adapter.TranslatePlaceholder(*phIdx)
		*phIdx++
		op := "<"
		if c.Not {
			op = ">="
		}
		return c.Field + " " + op + " " + ph, []any{c.Value}, nil

	case OpGt:
		ph := e.adapter.TranslatePlaceholder(*phIdx)
		*phIdx++
		op := ">"
		if c.Not {
			op = "<="
		}
		return c.Field + " " + op + " " + ph, []any{c.Value}, nil

	case OpLte:
		ph := e.adapter.TranslatePlaceholder(*phIdx)
		*phIdx++
		op := "<="
		if c.Not {
			op = ">"
		}
		return c.Field + " " + op + " " + ph, []any{c.Value}, nil

	case OpGte:
		ph := e.adapter.TranslatePlaceholder(*phIdx)
		*phIdx++
		op := ">="
		if c.Not {
			op = "<"
		}
		return c.Field + " " + op + " " + ph, []any{c.Value}, nil

	case OpLike:
		s, ok := c.Value.(string)
		if !ok {
			return "", nil, fmt.Errorf("LIKE requires string value, got %T", c.Value)
		}
		val := s
		if !c.RawValue {
			val = e.adapter.QuoteString(s)
		}
		ph := e.adapter.TranslatePlaceholder(*phIdx)
		*phIdx++
		// NOT + LIKE → NOT LIKE (валидно); "NOT"-префикс для LIKE допустим.
		// ESCAPE '\' обязателен: QuoteString экранирует %/_ обратным слэшем,
		// без ESCAPE-клаузы экранирование не работает (в SQLite \ — литерал,
		// % остаётся wildcard'ом → данные не находятся, DoS-защита неэффективна).
		op := "LIKE"
		if c.Not {
			op = "NOT LIKE"
		}
		return c.Field + " " + op + " " + ph + " ESCAPE '\\'", []any{val}, nil

	case OpILike:
		s, ok := c.Value.(string)
		if !ok {
			return "", nil, fmt.Errorf("ILIKE requires string value, got %T", c.Value)
		}
		val := s
		if !c.RawValue {
			val = e.adapter.QuoteString(s)
		}
		ph := e.adapter.TranslatePlaceholder(*phIdx)
		*phIdx++
		if e.isPostgres {
			// Postgres has native ILIKE — case-insensitive for all Unicode.
			op := "ILIKE"
			if c.Not {
				op = "NOT ILIKE"
			}
			return c.Field + " " + op + " " + ph + " ESCAPE '\\'", []any{val}, nil
		}
		// SQLite: LIKE is case-insensitive only for ASCII (A-Z).
		// Cyrillic and other Unicode needs COLLATE NOCASE for true
		// case-insensitive search.
		fieldExpr := c.Field + " COLLATE NOCASE"
		op := "LIKE"
		if c.Not {
			op = "NOT LIKE"
		}
		return fieldExpr + " " + op + " " + ph + " ESCAPE '\\'", []any{val}, nil

	case OpNotLike:
		s, ok := c.Value.(string)
		if !ok {
			return "", nil, fmt.Errorf("NOT LIKE requires string value, got %T", c.Value)
		}
		val := s
		if !c.RawValue {
			val = e.adapter.QuoteString(s)
		}
		ph := e.adapter.TranslatePlaceholder(*phIdx)
		*phIdx++
		return c.Field + " NOT LIKE " + ph + " ESCAPE '\\'", []any{val}, nil

	case OpRegex:
		s, ok := c.Value.(string)
		if !ok {
			return "", nil, fmt.Errorf("REGEXP requires string value, got %T", c.Value)
		}
		ph := e.adapter.TranslatePlaceholder(*phIdx)
		*phIdx++
		if e.isPostgres {
			// Postgres: ~ / !~ (оператор отрицания существует как токен).
			if c.Not {
				return c.Field + " !~ " + ph, []any{s}, nil
			}
			return c.Field + " ~ " + ph, []any{s}, nil
		}
		// SQLite: REGEXP / NOT REGEXP.
		if c.Not {
			return c.Field + " NOT REGEXP " + ph, []any{s}, nil
		}
		return c.Field + " REGEXP " + ph, []any{s}, nil

	case OpIn:
		if len(c.Values) == 0 {
			return "", nil, fmt.Errorf("IN requires at least one value")
		}
		phs := make([]string, len(c.Values))
		for i := range c.Values {
			phs[i] = e.adapter.TranslatePlaceholder(*phIdx)
			*phIdx++
		}
		if c.Not {
			return c.Field + " NOT IN (" + strings.Join(phs, ", ") + ")", c.Values, nil
		}
		return c.Field + " IN (" + strings.Join(phs, ", ") + ")", c.Values, nil

	case OpBetween:
		if len(c.Values) != 2 {
			return "", nil, fmt.Errorf("BETWEEN requires exactly 2 values, got %d", len(c.Values))
		}
		ph1 := e.adapter.TranslatePlaceholder(*phIdx)
		*phIdx++
		ph2 := e.adapter.TranslatePlaceholder(*phIdx)
		*phIdx++
		if c.Not {
			return c.Field + " NOT BETWEEN " + ph1 + " AND " + ph2, []any{c.Values[0], c.Values[1]}, nil
		}
		return c.Field + " BETWEEN " + ph1 + " AND " + ph2, []any{c.Values[0], c.Values[1]}, nil

	default:
		return "", nil, fmt.Errorf("unknown operator %d", c.Operator)
	}
}
