package config

import "strings"

// DefaultFilterableFieldRules returns the default rules for filterable fields.
// These represent fields that commonly make sense as filter parameters.
// Rules are combined with IsFilterableField's implicit rules:
// FK (*_id), business dates (*_date), business booleans (is_available, is_active).
func DefaultFilterableFieldRules() []FieldRule {
	return []FieldRule{
		{
			ID: "filterable.common",
			AllowNames: []string{
				"name", "article", "oem_number", "description",
				"price", "old_price", "category", "brand", "supplier",
				"label", "quantity", "status", "type", "active",
			},
			Reason: "Common filterable business fields",
		},
	}
}

// DefaultSearchableFieldRules returns the default rules for searchable (grep) fields.
// These are block rules: string fields that should NOT be searchable.
func DefaultSearchableFieldRules() []FieldRule {
	return []FieldRule{
		{
			ID:            "searchable.block_image",
			BlockSuffix:   []string{"_image", "_url"},
			BlockNames:    []string{"image", "thumbnail"},
			BlockContains: []string{"json", "seo"},
			Reason:        "Image/SEO/JSON fields are not searchable",
		},
	}
}

// DefaultEnumFieldRules returns the default rules for enum-like fields (distinct endpoint).
func DefaultEnumFieldRules() []FieldRule {
	return []FieldRule{
		{
			ID:            "enum.contains",
			AllowContains: []string{"status", "type", "role", "city", "country"},
			Reason:        "Columns that typically contain enum-like values",
		},
	}
}

// IsFilterableField checks whether a field should appear as an MCP filter parameter.
//
// Implicit always-true rules (universal/framework-level, not configurable):
//   - FK fields (*_id, except tenant_id for security)
//   - Business dates (*_date)
//   - Business booleans (is_available, is_active)
//
// Then rules from config are applied: if any rule matches, the field is filterable.
// Config FieldRules can add additional filterable fields or block specific ones.
func IsFilterableField(field EntityField, rules []FieldRule) bool {
	name := field.Name

	// Step 1: Block rules from config take priority over everything (security).
	// A block-only FieldRule (no allow patterns) unconditionally blocks matching names.
	for _, r := range rules {
		hasAllow := len(r.AllowNames) > 0 || len(r.AllowSuffix) > 0 || len(r.AllowContains) > 0
		if !hasAllow {
			// Block-only rule: check if name matches any block pattern
			for _, b := range r.BlockNames {
				if b == name {
					return false
				}
			}
			for _, s := range r.BlockSuffix {
				if strings.HasSuffix(name, s) {
					return false
				}
			}
			for _, c := range r.BlockContains {
				if strings.Contains(name, c) {
					return false
				}
			}
		}
	}

	// Step 2: Implicit always-true rules — these are universal, not autoparts-specific.
	if strings.HasSuffix(name, "_id") && name != "tenant_id" {
		return true // FK: every entity may reference others. tenant_id excluded for security.
	}
	if strings.HasSuffix(name, "_date") {
		return true // Business dates: order_date, delivery_date, etc.
	}
	if field.Type == FieldTypeBool && (name == "is_available" || name == "is_active") {
		return true // Business availability/activity flags
	}

	// Step 3: Configurable FieldRules (allow rules).
	for _, r := range rules {
		if r.Matches(name) {
			return true
		}
	}

	return false
}
