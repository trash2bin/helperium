// Package configgen — анализ колонок.
package configgen

import (
	"github.com/trash2bin/helperium/helperium-go/config"
)

// ── Default FieldRules — proxies ──────────────────────────────────────
// Functions moved to helperium-go/config/filterable.go.
// Local proxies keep call sites unchanged.

func DefaultFilterableFieldRules() []config.FieldRule { return config.DefaultFilterableFieldRules() }
func DefaultSearchableFieldRules() []config.FieldRule { return config.DefaultSearchableFieldRules() }
func DefaultEnumFieldRules() []config.FieldRule       { return config.DefaultEnumFieldRules() }

// hasSearchableFields проверяет, есть ли у entity string-поля для grep-поиска.
// rules — searchable block rules (default + custom).
func hasSearchableFields(entity config.Entity, rules []config.FieldRule) bool {
	for _, f := range entity.Fields {
		if f.PrimaryKey != nil && *f.PrimaryKey {
			continue
		}
		if f.ExcludeFromSearch {
			continue
		}
		if f.Column == "tenant_id" {
			continue
		}
		if f.Type != config.FieldTypeString {
			continue
		}
		// Field passes if it matches all rules (not blocked).
		// For block-only rules, Matches returns true if no block pattern matches.
		passes := true
		for _, r := range rules {
			if !r.Matches(f.Name) {
				passes = false
				break
			}
		}
		if passes {
			return true
		}
	}
	return false
}

// hasFilterableFields проверяет, есть ли у entity хоть одно поле, для которого
// имеет смысл filter-эндпоинт. Использует config.IsFilterableField для
// проверки (implicit rules + configurable FieldRules).
func hasFilterableFields(entity config.Entity, rules []config.FieldRule) bool {
	for _, f := range entity.Fields {
		if f.PrimaryKey != nil && *f.PrimaryKey {
			continue
		}
		if f.ExcludeFromSearch {
			continue
		}
		if f.Column == "tenant_id" {
			continue
		}
		if config.IsFilterableField(f, rules) {
			return true
		}
	}
	return false
}

// hasDataFields проверяет, есть ли у entity хоть одно non-PK поле.
func hasDataFields(entity config.Entity) bool {
	for _, f := range entity.Fields {
		if f.PrimaryKey != nil && *f.PrimaryKey {
			continue
		}
		return true
	}
	return false
}

// findEnumColumnsFromEntity ищет enum-подобные поля в Entity.Fields через FieldRules.
func findEnumColumnsFromEntity(entity config.Entity, rules []config.FieldRule) []string {
	var enums []string
	for _, f := range entity.Fields {
		if f.PrimaryKey != nil && *f.PrimaryKey {
			continue
		}
		if f.Type != config.FieldTypeString {
			continue
		}
		for _, r := range rules {
			if r.Matches(f.Name) {
				enums = append(enums, f.Name)
				break
			}
		}
	}
	return enums
}
