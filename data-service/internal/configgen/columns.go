// Package configgen — анализ колонок.
package configgen

import (
	"strings"

	"github.com/trash2bin/helperium/helperium-go/config"
)

// findEnumColumnsFromEntity ищет enum-подобные поля в Entity.Fields.
func findEnumColumnsFromEntity(entity config.Entity) []string {
	var enums []string
	for _, f := range entity.Fields {
		if f.PrimaryKey != nil && *f.PrimaryKey {
			continue
		}
		if f.Type != config.FieldTypeString {
			continue
		}
		lower := strings.ToLower(f.Name)
		switch {
		case strings.Contains(lower, "status"),
			strings.Contains(lower, "type"),
			strings.Contains(lower, "role"),
			strings.Contains(lower, "city"),
			strings.Contains(lower, "country"):
			enums = append(enums, f.Name)
		}
	}
	return enums
}
