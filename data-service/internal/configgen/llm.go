package configgen

import (
	"fmt"
	"strings"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// ── LLM-friendly schema ──

// SchemaForLLM — обселиченное описание схемы для LLM-агента.
// Не содержит raw SQL, только семантические типы и связи.
type SchemaForLLM struct {
	// Entities — список сущностей, доступных агенту. Каждая сущность — это
	// таблица, но абстрагированная через бизнес-имя и семантические типы.
	Entities []LLMEntity `json:"entities"`

	// WorkflowHints — стратегические подсказки агенту: как искать,
	// какие связи использовать, какие тулы вызывать.
	WorkflowHints []string `json:"workflow_hints,omitempty"`
}

// LLMEntity — описание одной сущности для LLM.
type LLMEntity struct {
	// Name — бизнес-имя сущности ("Товар (catalog_product)").
	Name string `json:"name"`

	// ToolPrefix — префикс для тулов, ссылающихся на эту сущность ("catalog_product").
	// Нужен для построения правильных ссылок get_*, grep_*, filter_*, schema_*.
	ToolPrefix string `json:"-"`

	// Description — комментарий из БД либо авто-описание.
	Description string `json:"description,omitempty"`

	// SearchFields — поля, по которым работает нечёткий поиск (ILIKE/LIKE).
	// Агент может передавать текст в pattern-параметр grep_* тула.
	SearchFields string `json:"search_fields,omitempty"`

	// FilterFields — поля для точной фильтрации, сгруппированные по типу.
	FilterFields []FilterGroup `json:"filter_fields,omitempty"`

	// Relations — связи с другими сущностями (FK).
	Relations []LLMRelation `json:"relations,omitempty"`
}

// FilterGroup — группа фильтров одного типа.
type FilterGroup struct {
	// Label — "exact" / "bool" / "range" / "text search" / "enum".
	Label string `json:"label"`

	// Fields — список колонок с описанием.
	Fields []FilterField `json:"fields"`
}

// FilterField — одна колонка-фильтр.
type FilterField struct {
	Name        string `json:"name"`
	Column      string `json:"column"` // оригинальное имя в БД (snake_case)
	Type        string `json:"type"`   // string/int/float/bool/date/enum
	Description string `json:"description,omitempty"`
	IsFK        bool   `json:"is_fk,omitempty"`     // true если это внешний ключ
	FKEntity    string `json:"fk_entity,omitempty"` // имя сущности, на которую ссылается FK
}

// LLMRelation — связь с другой сущностью.
type LLMRelation struct {
	// Field — колонка в текущей таблице (FK).
	Field string `json:"field"`

	// ReferencedEntity — имя связанной сущности.
	ReferencedEntity string `json:"referenced_entity"`

	// ReferencedTool — тул для навигации к связанным данным.
	ReferencedTool string `json:"referenced_tool,omitempty"`
}

// GenerateSchemaForLLM превращает datasource.Schema в обселиченное
// описание для LLM-агента. Никакого raw SQL.
//
// cfg — сгенерированный config.Config (нужен для entities, endpoints, FK).
func GenerateSchemaForLLM(schema *datasource.Schema, cfg *config.Config) *SchemaForLLM {
	// Resolve the display and plural config
	displayPrefixes := cfg.DisplayPrefixes
	if len(displayPrefixes) == 0 {
		displayPrefixes = DefaultDisplayPrefixes()
	}
	// Custom short names from config
	customShortNames := cfg.CustomShortNames
	if customShortNames == nil {
		customShortNames = make(map[string]string)
	}
	// schema может быть nil (db_map после рестарта data-service до rewrite).
	// Если entities есть — строим из cfg.Entities (FK из Relations);
	// если и entities пусты — возвращаем пустую карту.
	if schema == nil && len(cfg.Entities) == 0 {
		return &SchemaForLLM{Entities: []LLMEntity{}}
	}

	// Build entity map from config (shortName -> Entity)
	entityMap := make(map[string]config.Entity)
	for _, e := range cfg.Entities {
		entityMap[e.Name] = e
	}

	// Build table -> entity name map
	tableToEntity := make(map[string]string)
	for _, e := range cfg.Entities {
		short := e.Name
		full := e.Table
		tableToEntity[full] = short
		// Also index by short name
		tableToEntity[short] = short
	}

	// Build FK index: (tableName, column) -> referenced table
	fkIndex := make(map[[2]string]string) // key: (table, column) -> referencedTable
	if schema != nil {
		for _, tbl := range schema.Tables {
			for _, fk := range tbl.ForeignKeys {
				for i, col := range fk.Columns {
					if i < len(fk.ReferencedColumns) {
						fkIndex[[2]string{tbl.Name, col}] = fk.ReferencedTable
					}
				}
			}
		}
	} else {
		// Fallback (Фаза 2.5 smoke): без introspected schema (db_map после
		// рестарта data-service, пока админ не вызвал rewrite) строим FK-индекс
		// из cfg.Entities.Relations — они есть в конфиге всегда.
		for _, e := range cfg.Entities {
			for _, rel := range e.Relations {
				if rel.LocalFK != "" {
					fkIndex[[2]string{e.Table, rel.LocalFK}] = rel.Table
				}
			}
		}
	}

	// Build entity -> relation index from config.Relation
	entityRelations := make(map[string][]config.Relation)
	for _, e := range cfg.Entities {
		if len(e.Relations) > 0 {
			entityRelations[e.Name] = append(entityRelations[e.Name], e.Relations...)
		}
	}

	entities := make([]LLMEntity, 0, len(cfg.Entities))
	hints := []string{}
	hintSet := make(map[string]bool)

	for _, e := range cfg.Entities {
		// Find the original datasource.Table for this entity
		// (nil если schema не заинтроспекчена — поля берём из cfg.Entities).
		var tbl *datasource.Table
		if schema != nil {
			for i := range schema.Tables {
				stripped := schema.Tables[i].Name
				if idx := strings.LastIndex(stripped, "."); idx >= 0 {
					stripped = stripped[idx+1:]
				}
				if stripped == e.Name || schema.Tables[i].Name == e.Table {
					tbl = &schema.Tables[i]
					break
				}
			}
		}
		if tbl == nil && schema != nil {
			continue
		}

		// Build name and description
		businessName := shortBusinessName(e.Name, displayPrefixes, customShortNames)
		displayName := fmt.Sprintf("%s (%s)", businessName, e.Name)

		desc := e.Description
		if desc == "" {
			desc = fmt.Sprintf("Таблица %s", e.Name)
		}

		// Filter fields — group by type
		exactFields := make([]FilterField, 0)
		boolFields := make([]FilterField, 0)
		rangeFields := make([]FilterField, 0)

		for _, f := range e.Fields {
			isPK := f.PrimaryKey != nil && *f.PrimaryKey
			if isPK {
				continue
			}

			// Check FK. tbl может быть nil (schema не заинтроспекчена) —
			// тогда FK-поиск идёт по e.Table/e.Name (индекс построен из Relations).
			var fkRef string
			if tbl != nil {
				fkRef = fkIndex[[2]string{tbl.Name, f.Column}]
				if fkRef == "" {
					// Also try short table name
					short := tbl.Name
					if idx := strings.LastIndex(short, "."); idx >= 0 {
						short = short[idx+1:]
					}
					fkRef = fkIndex[[2]string{short, f.Column}]
				}
			}
			if fkRef == "" {
				fkRef = fkIndex[[2]string{e.Name, f.Column}]
				if fkRef == "" {
					fkRef = fkIndex[[2]string{e.Table, f.Column}]
				}
			}

			// Resolve FK entity name
			fkEntity := ""
			if fkRef != "" {
				if refShort := tableToEntity[fkRef]; refShort != "" {
					fkEntity = shortBusinessName(refShort, displayPrefixes, customShortNames)
				} else {
					short := fkRef
					if idx := strings.LastIndex(short, "."); idx >= 0 {
						short = short[idx+1:]
					}
					fkEntity = shortBusinessName(short, displayPrefixes, customShortNames)
				}
			}

			fieldDesc := f.Description
			if fkEntity != "" {
				if fieldDesc != "" {
					fieldDesc += " | "
				}
				fieldDesc += fmt.Sprintf("FK → %s (используй поиск по %s)", fkEntity, fkEntity)
			}

			ff := FilterField{
				Name:        shortColumnName(f.Name),
				Column:      f.Column,
				Type:        string(f.Type),
				Description: fieldDesc,
				IsFK:        fkRef != "",
				FKEntity:    fkEntity,
			}

			switch f.Type {
			case config.FieldTypeBool:
				boolFields = append(boolFields, ff)
			case config.FieldTypeInt, config.FieldTypeFloat:
				rangeFields = append(rangeFields, ff)
			default:
				exactFields = append(exactFields, ff)
			}
		}

		// Relations from config
		relations := make([]LLMRelation, 0)
		for _, rel := range entityRelations[e.Name] {
			targetName := rel.Table
			if targetShort := tableToEntity[rel.Table]; targetShort != "" {
				targetName = targetShort
			}
			relations = append(relations, LLMRelation{
				Field:            rel.LocalFK,
				ReferencedEntity: shortBusinessName(targetName, displayPrefixes, customShortNames),
			})
		}

		// Build filter groups
		filterFields := make([]FilterGroup, 0)
		if len(boolFields) > 0 {
			filterFields = append(filterFields, FilterGroup{Label: "bool", Fields: boolFields})
		}
		if len(rangeFields) > 0 {
			filterFields = append(filterFields, FilterGroup{Label: "range", Fields: rangeFields})
		}
		if len(exactFields) > 0 {
			filterFields = append(filterFields, FilterGroup{Label: "exact", Fields: exactFields})
		}

		// SearchFields — поля нечёткого поиска (grep). Зеркально stringFields
		// в search-пакете: string-тип, не PK, не tenant_id, не ExcludeFromSearch,
		// проходит searchableRules. Раньше всегда "" — модель не знала,
		// по каким полям искать.
		searchable := make([]string, 0, len(e.Fields))
		for _, f := range e.Fields {
			if f.PrimaryKey != nil && *f.PrimaryKey {
				continue
			}
			if f.Column == "tenant_id" {
				continue
			}
			if f.ExcludeFromSearch {
				continue
			}
			if f.Type != config.FieldTypeString {
				continue
			}
			searchable = append(searchable, f.Column)
		}

		entities = append(entities, LLMEntity{
			Name:         displayName,
			ToolPrefix:   e.Name, // e.g. "catalog_product"
			Description:  desc,
			SearchFields: strings.Join(searchable, ", "),
			FilterFields: filterFields,
			Relations:    relations,
		})
	}

	// Generate workflow hints
	hintKey := func(h string) string {
		return strings.ToLower(strings.TrimSpace(h))
	}

	// Доменно-нейтральные подсказки. Ссылаются на реальные тулы: db_map,
	// db_describe, db_search, db_get + пер-энтити filter_<entity> (Фаза 2.5 smoke:
	// filter деконсолидирован, т.к. имена полей нужны модели прямо в схеме тула).
	// Для filter используем паттерн filter_<entity>, т.к. имя зависит от entity.

	// 1. Search-first: не перебирать по ID.
	searchFirst := "SEARCH-FIRST: NEVER guess an id and call db_get on it. Always search first with db_search (text) or filter_<entity> (exact values), then use the id from the result with db_get. Sequential id enumeration (db_get id=1, id=2, ...) is forbidden and wastes quota."
	if !hintSet[hintKey(searchFirst)] {
		hints = append(hints, searchFirst)
		hintSet[hintKey(searchFirst)] = true
	}

	// 2. Efficient workflow: db_map → filter_<entity>/db_search.
	efficient := "EFFICIENT WORKFLOW: start with db_map to see the entities. For exact filtering, use filter_<entity> (replace <entity> with the entity name — its schema lists all filterable fields). For text search, use db_search. Use db_describe to see valid values/ranges."
	if !hintSet[hintKey(efficient)] {
		hints = append(hints, efficient)
		hintSet[hintKey(efficient)] = true
	}

	// 3. Self-correction: db_describe as the discovery tool.
	selfCorrection := "SELF-CORRECTION: if db_search returns empty results, use db_describe on the same entity to discover valid values first. If filter_<entity> returns nothing, check field names (they are listed in the filter tool schema) or values via db_describe. Never call a tool without parameters."
	if !hintSet[hintKey(selfCorrection)] {
		hints = append(hints, selfCorrection)
		hintSet[hintKey(selfCorrection)] = true
	}

	return &SchemaForLLM{
		Entities:      entities,
		WorkflowHints: hints,
	}
}
