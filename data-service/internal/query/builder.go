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

	// 4. ORDER BY
	if len(plan.Order) > 0 {
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

	// 5. LIMIT / OFFSET
	if plan.Limit > 0 {
		b.WriteString(" LIMIT ")
		b.WriteString(e.adapter.TranslatePlaceholder(phIdx))
		args = append(args, plan.Limit)
		phIdx++
	}
	if plan.Offset > 0 {
		b.WriteString(" OFFSET ")
		b.WriteString(e.adapter.TranslatePlaceholder(phIdx))
		args = append(args, plan.Offset)
		phIdx++
	}

	return b.String(), args, nil
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
	notPrefix := ""
	if c.Not {
		notPrefix = "NOT "
	}

	switch c.Operator {
	case OpEq:
		ph := e.adapter.TranslatePlaceholder(*phIdx)
		*phIdx++
		return c.Field + " " + notPrefix + "= " + ph, []any{c.Value}, nil

	case OpNeq:
		ph := e.adapter.TranslatePlaceholder(*phIdx)
		*phIdx++
		return c.Field + " " + notPrefix + "!= " + ph, []any{c.Value}, nil

	case OpLt:
		ph := e.adapter.TranslatePlaceholder(*phIdx)
		*phIdx++
		return c.Field + " " + notPrefix + "< " + ph, []any{c.Value}, nil

	case OpGt:
		ph := e.adapter.TranslatePlaceholder(*phIdx)
		*phIdx++
		return c.Field + " " + notPrefix + "> " + ph, []any{c.Value}, nil

	case OpLte:
		ph := e.adapter.TranslatePlaceholder(*phIdx)
		*phIdx++
		return c.Field + " " + notPrefix + "<= " + ph, []any{c.Value}, nil

	case OpGte:
		ph := e.adapter.TranslatePlaceholder(*phIdx)
		*phIdx++
		return c.Field + " " + notPrefix + ">= " + ph, []any{c.Value}, nil

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
		return c.Field + " " + notPrefix + "LIKE " + ph, []any{val}, nil

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
			return c.Field + " " + notPrefix + "ILIKE " + ph, []any{val}, nil
		}
		// SQLite: LIKE is case-insensitive only for ASCII (A-Z).
		// Cyrillic and other Unicode needs COLLATE NOCASE for true
		// case-insensitive search.
		fieldExpr := c.Field + " COLLATE NOCASE"
		return fieldExpr + " " + notPrefix + "LIKE " + ph, []any{val}, nil

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
		return c.Field + " NOT LIKE " + ph, []any{val}, nil

	case OpRegex:
		s, ok := c.Value.(string)
		if !ok {
			return "", nil, fmt.Errorf("REGEXP requires string value, got %T", c.Value)
		}
		ph := e.adapter.TranslatePlaceholder(*phIdx)
		*phIdx++
		op := "REGEXP"
		if e.isPostgres {
			op = "~"
		}
		return c.Field + " " + notPrefix + op + " " + ph, []any{s}, nil

	case OpIn:
		if len(c.Values) == 0 {
			return "", nil, fmt.Errorf("IN requires at least one value")
		}
		phs := make([]string, len(c.Values))
		for i := range c.Values {
			phs[i] = e.adapter.TranslatePlaceholder(*phIdx)
			*phIdx++
		}
		return c.Field + " " + notPrefix + "IN (" + strings.Join(phs, ", ") + ")", c.Values, nil

	case OpBetween:
		if len(c.Values) != 2 {
			return "", nil, fmt.Errorf("BETWEEN requires exactly 2 values, got %d", len(c.Values))
		}
		ph1 := e.adapter.TranslatePlaceholder(*phIdx)
		*phIdx++
		ph2 := e.adapter.TranslatePlaceholder(*phIdx)
		*phIdx++
		return c.Field + " " + notPrefix + "BETWEEN " + ph1 + " AND " + ph2, []any{c.Values[0], c.Values[1]}, nil

	default:
		return "", nil, fmt.Errorf("unknown operator %d", c.Operator)
	}
}
