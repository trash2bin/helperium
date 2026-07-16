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
	// Нужен для построения правильных ссылок на find_*, get_*, list_*.
	ToolPrefix string `json:"-"`

	// Description — комментарий из БД либо авто-описание.
	Description string `json:"description,omitempty"`

	// SearchFields — поля, по которым работает нечёткий поиск (ILIKE/LIKE).
	// Агент может передавать текст в name-параметр find_* тула.
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
	Column      string `json:"column"`      // оригинальное имя в БД (snake_case)
	Type        string `json:"type"`         // string/int/float/bool/date/enum
	Description string `json:"description,omitempty"`
	IsFK        bool   `json:"is_fk,omitempty"`   // true если это внешний ключ
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

// compactFilterSummary builds a grouped description of filter fields.
// Groups: search (partial), exact (string/int/float), bool (true/false).
func compactFilterSummary(ent *config.Entity) string {
	if ent == nil || len(ent.Fields) == 0 {
		return ""
	}
	var searchFields, exactFields, boolFields []string
	for _, f := range ent.Fields {
		if f.PrimaryKey != nil && *f.PrimaryKey {
			continue
		}
		if f.Name == "limit" || f.Name == "offset" {
			continue
		}
		lower := f.Name
		isSearch := lower == "name" || strings.HasSuffix(lower, "_name") || strings.HasSuffix(lower, "_title") || strings.HasPrefix(lower, "name")
		if isSearch && f.Type == config.FieldTypeString {
			searchFields = append(searchFields, lower)
		} else if f.Type == config.FieldTypeBool {
			boolFields = append(boolFields, lower)
		} else {
			exactFields = append(exactFields, lower)
		}
	}
	var parts []string
	if len(searchFields) > 0 {
		parts = append(parts, fmt.Sprintf("partial match on '%s'", strings.Join(searchFields, ", ")))
	}
	if len(exactFields) > 0 {
		show := exactFields
		if len(show) > 3 {
			show = show[:3]
			show = append(show, fmt.Sprintf("+%d more", len(exactFields)-3))
		}
		parts = append(parts, fmt.Sprintf("exact: %s", strings.Join(show, ", ")))
	}
	if len(boolFields) > 0 {
		parts = append(parts, fmt.Sprintf("bool: %s", strings.Join(boolFields, ", ")))
	}
	return strings.Join(parts, "; ")
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
	customPlurals := cfg.CustomPlurals
	if customPlurals == nil {
		customPlurals = make(map[string]string)
	}
	if schema == nil {
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
	for _, tbl := range schema.Tables {
		for _, fk := range tbl.ForeignKeys {
			for i, col := range fk.Columns {
				if i < len(fk.ReferencedColumns) {
					fkIndex[[2]string{tbl.Name, col}] = fk.ReferencedTable
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
		var tbl *datasource.Table
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
		if tbl == nil {
			continue
		}

		// Build name and description
		businessName := shortBusinessName(e.Name, displayPrefixes)
		displayName := fmt.Sprintf("%s (%s)", businessName, e.Name)

		desc := e.Description
		if desc == "" {
			desc = fmt.Sprintf("Таблица %s", e.Name)
		}

		// Search fields
		var searchFields []string
		for _, ep := range cfg.Endpoints {
			if ep.Entity == e.Name && ep.SearchField != "" {
				searchFields = append(searchFields, ep.SearchField)
			}
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

			// Check FK
			fkRef := fkIndex[[2]string{tbl.Name, f.Column}]
			if fkRef == "" {
				// Also try short table name
				short := tbl.Name
				if idx := strings.LastIndex(short, "."); idx >= 0 {
					short = short[idx+1:]
				}
				fkRef = fkIndex[[2]string{short, f.Column}]
				if fkRef == "" {
					fkRef = fkIndex[[2]string{e.Name, f.Column}]
				}
			}

			// Resolve FK entity name
			fkEntity := ""
			if fkRef != "" {
				if refShort := tableToEntity[fkRef]; refShort != "" {
					fkEntity = shortBusinessName(refShort, displayPrefixes)
				} else {
					short := fkRef
					if idx := strings.LastIndex(short, "."); idx >= 0 {
						short = short[idx+1:]
					}
					fkEntity = shortBusinessName(short, displayPrefixes)
				}
			}

			// Check if search field (already handled above, skip from filters)
			isSearch := false
			for _, sf := range searchFields {
				if sf == f.Column || sf == f.Name {
					isSearch = true
					break
				}
			}
			if isSearch {
				continue
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
				ReferencedEntity: shortBusinessName(targetName, displayPrefixes),
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

		entities = append(entities, LLMEntity{
			Name:         displayName,
			ToolPrefix:   e.Name, // e.g. "catalog_product"
			Description:  desc,
			SearchFields: strings.Join(searchFields, ", "),
			FilterFields: filterFields,
			Relations:    relations,
		})
	}

	// Generate workflow hints
	hintKey := func(h string) string {
		return strings.ToLower(strings.TrimSpace(h))
	}

	// Check for category-like vs brand-like entities
	hasCategory := false
	hasBrand := false
	for _, e := range entities {
		low := strings.ToLower(e.Name)
		if strings.Contains(low, "категори") || strings.Contains(low, "category") {
			hasCategory = true
		}
		if strings.Contains(low, "бренд") || strings.Contains(low, "brand") {
			hasBrand = true
		}
	}

	if hasCategory && hasBrand {
		h := "Categories = part type (brake pads, shock absorbers). Brands = manufacturer (Bosch, KYB, TRW). First find the category, then find products via products_by_category."
		if !hintSet[hintKey(h)] {
			hints = append(hints, h)
			hintSet[hintKey(h)] = true
		}
	} else if hasCategory {
		h := "Categories = part type. First find the category, then find products via products_by_category."
		if !hintSet[hintKey(h)] {
			hints = append(hints, h)
			hintSet[hintKey(h)] = true
		}
	}

	return &SchemaForLLM{
		Entities:      entities,
		WorkflowHints: hints,
	}
}
