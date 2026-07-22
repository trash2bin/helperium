package search

import (
	"fmt"
	"net/http"
	"strconv"
	"strings"

	"github.com/trash2bin/helperium/data-service/internal/query"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// GrepStrategy — text search strategy with tokenization and multi-field OR.
//
// LLM-facing name: grep_{entity}
// Parameters: pattern (required), ignore_case (default true), fields, invert,
// regex, limit (default 10, max 1000), offset, format, sort_by.
type GrepStrategy struct {
	// ── Security limits ────────────────────────────────────────────────
	// maxRegexLen — максимальная длина regex-паттерна (ReDoS защита).
	maxRegexLen int
	// maxTokens — максимальное количество токенов для multi-token поиска.
	maxTokens int
	// maxFields — максимальное количество полей в fields параметре.
	maxFields int

	// idCol — имя ID-колонки для compact format.
	idCol string
	// nameCol — имя name-колонки для compact format.
	nameCol string
}

// NewGrepStrategy creates a GrepStrategy.
// idCol and nameCol are used for compact format output.
// NewGrepStrategy creates a GrepStrategy with security limits.
// idCol and nameCol are used for compact format output.
func NewGrepStrategy(idCol, nameCol string) *GrepStrategy {
	return &GrepStrategy{
		idCol:      idCol,
		nameCol:    nameCol,
		maxRegexLen: 200,  // ReDoS защита: макс 200 символов
		maxTokens:   10,   // макс 10 токенов
		maxFields:   20,   // макс 20 полей
	}
}

func (s *GrepStrategy) Name() string { return "grep" }

func (s *GrepStrategy) EntityIDCol() string   { return s.idCol }
func (s *GrepStrategy) EntityNameCol() string { return s.nameCol }

func (s *GrepStrategy) ToolName(entity config.Entity) string {
	return "grep_" + entity.Name
}

func (s *GrepStrategy) ToolDescription(entity config.Entity) string {
	return fmt.Sprintf(
		"Search %s by text.\n"+
			"\n"+
			"REQUIRED: Always pass 'pattern' parameter (what to search). Returns error if missing!\n"+
			"\n"+
			"Examples:\n"+
			"  pattern='muffler BMW'     -> finds parts with BOTH 'muffler' AND 'BMW'\n"+
			"  pattern='oil', limit=5     -> first 5 results\n"+
			"  pattern='Brembo', fields='name,description' -> search in name+description only\n"+
			"\n"+
			"SQLite note: Cyrillic search is case-sensitive - try capitalized form.\n"+
			"See doc for: ignore_case, invert, regex, format, offset, sort_by (not in JSON Schema)",
		entity.Name,
	)
}

func (s *GrepStrategy) ToolParams(entity config.Entity) []config.EndpointParam {
	f := false
	t := true

	params := []config.EndpointParam{
		{Name: "pattern", In: config.ParamInQuery, Type: config.ParamTypeString, Required: &t,
			Description: "Search query. REQUIRED. Example: 'muffler BMW', 'oil', 'Brembo'."},
		{Name: "limit", In: config.ParamInQuery, Type: config.ParamTypeInt, Required: &f,
			Description: "Max results (1-100, default: 10)."},
		{Name: "fields", In: config.ParamInQuery, Type: config.ParamTypeString, Required: &f,
			Description: "Comma-separated field names to search. Default: all string fields. Example: 'name,description'"},
	}
	return params
}

// ParseRequest разбирает HTTP-запрос в QueryPlan для grep-стратегии.
func (s *GrepStrategy) ParseRequest(r *http.Request, entity config.Entity, a Adapter) (*query.QueryPlan, error) {
	q := r.URL.Query()

	// ── Pattern ─────────────────────────────────────────────────────
	pattern := strings.TrimSpace(q.Get("pattern"))
	if pattern == "" {
		return nil, fmt.Errorf("'pattern' is required. Example: pattern='muffler BMW' or pattern='oil',limit=5")
	}

	// ── Параметры ───────────────────────────────────────────────────
	ignoreCase := parseBoolParam(q, "ignore_case", true)
	regex := parseBoolParam(q, "regex", false)
	invert := parseBoolParam(q, "invert", false)

	// ── SECURITY: ReDoS защита ────────────────────────────────────
	if regex && len(pattern) > s.maxRegexLen {
		return nil, fmt.Errorf("regex pattern too long: %d chars (max %d)", len(pattern), s.maxRegexLen)
	}

	// ── SECURITY: кап на количество токенов ────────────────────────
	fieldsStr := strings.TrimSpace(q.Get("fields"))
	var searchFields []config.EntityField
	if fieldsStr != "" {
		// Пользовательские поля: разбираем, ищем в entity.
		names := strings.Split(fieldsStr, ",")
		// SECURITY: кап на количество полей
		if len(names) > s.maxFields {
			return nil, fmt.Errorf("too many fields: %d (max %d)", len(names), s.maxFields)
		}
		fieldSet := make(map[string]bool, len(names))
		for _, n := range names {
			fieldSet[strings.TrimSpace(n)] = true
		}
		for _, f := range entity.Fields {
			if fieldSet[f.Name] {
				searchFields = append(searchFields, f)
			}
		}
		if len(searchFields) == 0 {
			// Fallback на все string поля, если указанные не найдены.
			searchFields = stringFields(entity)
		}
	} else {
		searchFields = stringFields(entity)
	}

	if len(searchFields) == 0 {
		// Нет строковых полей для поиска — возвращаем list всех записей.
		return s.listPlan(q, entity, a), nil
	}

	// ── SECURITY: лимит на кол-во искомых полей ──────────────────────
	if len(searchFields) > s.maxFields {
		searchFields = searchFields[:s.maxFields]
	}

	// ── Токенизация ─────────────────────────────────────────────────
	tokens := tokenize(pattern)
	if len(tokens) == 0 {
		return s.listPlan(q, entity, a), nil
	}
	// SECURITY: кап на количество токенов
	if len(tokens) > s.maxTokens {
		tokens = tokens[:s.maxTokens]
	}

	// ── LIKE escaped values ─────────────────────────────────────────
	// Если regex=false: каждый токен оборачиваем в %token%, с экранированием.
	// Если regex=true: весь pattern идёт одним regex-выражением.
	var whereParts []string
	var args []any
	phIdx := 1

	if regex {
		// Regex: весь pattern как одно выражение, OR по полям.
		reOp := regexOp(a)
		if invert {
			reOp = "!" + reOp
		}
		fieldClauses := make([]string, 0, len(searchFields))
		for _, f := range searchFields {
			qName := a.QuoteIdentifier(f.Column)
			ph := a.TranslatePlaceholder(phIdx)
			phIdx++
			fieldClauses = append(fieldClauses, qName+" "+reOp+" "+ph)
			args = append(args, pattern)
		}
		whereParts = append(whereParts, "("+strings.Join(fieldClauses, " OR ")+")")
	} else {
		// LIKE / ILIKE: multi-token AND внутри одного поля, OR между полями.
		//
		// SQLite LIKE is case-insensitive only for ASCII (A-Z).
		// For cyrillic (and other non-ASCII), we use COLLATE NOCASE
		// to ensure true case-insensitive search. Postgres uses ILIKE.
		likeOp := "LIKE"
		collateNocase := false
		if ignoreCase {
			if a.IsPostgres() {
				likeOp = "ILIKE"
			} else {
				// SQLite: COLLATE NOCASE for cyrillic support
				collateNocase = true
			}
		}
		if invert {
			likeOp = "NOT " + likeOp
		}

		fieldClauses := make([]string, 0, len(searchFields))
		for _, f := range searchFields {
			qName := a.QuoteIdentifier(f.Column)
			if collateNocase {
				qName = qName + " COLLATE NOCASE"
			}
			tokenClauses := make([]string, 0, len(tokens))
			for _, tok := range tokens {
				escaped := a.QuoteString(tok)
				val := "%" + escaped + "%"
				ph := a.TranslatePlaceholder(phIdx)
				phIdx++
				tokenClauses = append(tokenClauses, qName+" "+likeOp+" "+ph)
				args = append(args, val)
			}
			// AND всех токенов внутри поля.
			fieldClauses = append(fieldClauses, "("+strings.Join(tokenClauses, " AND ")+")")
		}
		// OR между полями.
		whereParts = append(whereParts, strings.Join(fieldClauses, " OR "))
	}

	plan := &query.QueryPlan{
		Select:      selectClause(entity, q, a),
		From:        a.QuoteIdentifier(entity.Table),
		RawWhere:    strings.Join(whereParts, " AND "),
		RawWhereArgs: args,
		Limit:       parseLimit(q),
		Offset:      parseOffset(q),
		Order:       parseOrder(q, entity, a),
		Format:      parseFormat(q),
	}
	return plan, nil
}

// listPlan создаёт QueryPlan без условий (list всех записей).
func (s *GrepStrategy) listPlan(q map[string][]string, entity config.Entity, a Adapter) *query.QueryPlan {
	return &query.QueryPlan{
		Select: selectClause(entity, q, a),
		From:   a.QuoteIdentifier(entity.Table),
		Limit:  parseLimit(q),
		Offset: parseOffset(q),
		Order:  parseOrder(q, entity, a),
		Format: parseFormat(q),
	}
}

// =============================================================================
// Helpers
// =============================================================================

// stringFields возвращает все строковые поля сущности (кроме PK).
func stringFields(entity config.Entity) []config.EntityField {
	var result []config.EntityField
	for _, f := range entity.Fields {
		if f.PrimaryKey != nil && *f.PrimaryKey {
			continue
		}
		if f.Type == config.FieldTypeString {
			result = append(result, f)
		}
	}
	return result
}

// regexOp returns the regex operator for the database.
func regexOp(a Adapter) string {
	if a.IsPostgres() {
		return "~"
	}
	return "REGEXP"
}

// tokenize разбивает строку на слова (по пробелам) и обрезает каждое.
func tokenize(s string) []string {
	parts := strings.Fields(s)
	if len(parts) == 0 {
		return nil
	}
	return parts
}

// parseBoolParam извлекает bool из query params с заданным default.
func parseBoolParam(q map[string][]string, name string, def bool) bool {
	vals, ok := q[name]
	if !ok || len(vals) == 0 {
		return def
	}
	switch strings.ToLower(strings.TrimSpace(vals[0])) {
	case "true", "1", "yes":
		return true
	case "false", "0", "no":
		return false
	default:
		return def
	}
}

// parseLimit извлекает limit из query params.
func parseLimit(q map[string][]string) int {
	vals, ok := q["limit"]
	if !ok || len(vals) == 0 {
		return 10
	}
	v, err := strconv.Atoi(strings.TrimSpace(vals[0]))
	if err != nil || v <= 0 {
		return 10
	}
	if v > 1000 {
		return 1000
	}
	return v
}

// parseOffset извлекает offset из query params.
func parseOffset(q map[string][]string) int {
	vals, ok := q["offset"]
	if !ok || len(vals) == 0 {
		return 0
	}
	v, err := strconv.Atoi(strings.TrimSpace(vals[0]))
	if err != nil || v < 0 {
		return 0
	}
	return v
}

// parseFormat извлекает format из query params.
func parseFormat(q map[string][]string) query.ResponseFormat {
	vals, ok := q["format"]
	if !ok || len(vals) == 0 {
		return query.FormatCompact
	}
	switch strings.ToLower(strings.TrimSpace(vals[0])) {
	case "full":
		return query.FormatFull
	case "count":
		return query.FormatCount
	default:
		return query.FormatCompact
	}
}

// parseOrder разбирает sort_by в OrderClause.
func parseOrder(q map[string][]string, entity config.Entity, a Adapter) []query.OrderClause {
	vals, ok := q["sort_by"]
	if !ok || len(vals) == 0 {
		return nil
	}
	sortBy := strings.TrimSpace(vals[0])
	if sortBy == "" {
		return nil
	}

	desc := false
	fieldName := sortBy
	if strings.HasPrefix(sortBy, "-") {
		desc = true
		fieldName = strings.TrimPrefix(sortBy, "-")
	}

	// Найти колонку по публичному имени.
	colName := findColumn(entity, fieldName)
	if colName == "" {
		return nil
	}
	return []query.OrderClause{
		{Field: a.QuoteIdentifier(colName), Desc: desc},
	}
}

// findColumn ищет имя колонки БД по публичному имени поля.
func findColumn(entity config.Entity, fieldName string) string {
	for _, f := range entity.Fields {
		if f.Name == fieldName {
			return f.Column
		}
	}
	return ""
}

// selectClause выбирает колонки для SELECT.
func selectClause(entity config.Entity, q map[string][]string, a Adapter) query.SelectClause {
	format := parseFormat(q)
	switch format {
	case query.FormatFull:
		cols := make([]string, 0, len(entity.Fields))
		for _, f := range entity.Fields {
			cols = append(cols, a.QuoteIdentifier(f.Column))
		}
		return query.SelectClause{Columns: cols}
	case query.FormatCount:
		return query.SelectClause{}
	default: // compact
		// Для compact: только id + первое строковое поле.
		cols := []string{a.QuoteIdentifier(entity.IDColumn)}
		for _, f := range entity.Fields {
			if f.Type == config.FieldTypeString {
				cols = append(cols, a.QuoteIdentifier(f.Column))
				break
			}
		}
		return query.SelectClause{Columns: cols}
	}
}
