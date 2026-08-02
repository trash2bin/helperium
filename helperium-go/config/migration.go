// Package config — config version migration.
//
// Normalize brings any historical config version to the current schema.
// Migration steps are chained: v0 → v1 → v2 → ... → CurrentVersion.
// Each step is idempotent and handles only its own delta.

package config

import "encoding/json"

// CurrentConfigVersion is the latest config schema version.
// Increment when introducing a breaking change to the config structure.
const CurrentConfigVersion = 4

// Normalize upgrades the config to CurrentConfigVersion.
// Fields that do not exist in older versions are backfilled with safe defaults.
// The receiver is mutated in place — call BEFORE Validate().
func (c *Config) Normalize() {
	// ── Version defaults ──────────────────────────────────────────
	if c.Version == 0 {
		c.Version = 1
	}

	// ── Chain: every step brings us closer to CurrentConfigVersion ─
	for c.Version < CurrentConfigVersion {
		switch c.Version {
		case 1:
			c.normalizeV1ToV2()
		case 2:
			c.normalizeV2ToV3()
		case 3:
			c.normalizeV3ToV4()
		default:
			// Unknown version — pin to current and continue.
			c.Version = CurrentConfigVersion
		}
	}
}

// normalizeV1ToV2 upgrades v1 → v2 configs.
//
// Changes in v2:
//   - Meta block (generated_at, generator_version, config_version)
//   - Relation.JunctionTable for many_to_many
//   - EndpointParam.ArrayOf for array-type params
//   - EndpointParam.EnumValues for enum params
//   - ApprovedTools migrated from []string to []ApprovedTool
func (c *Config) normalizeV1ToV2() {
	// 1. Backfill Meta
	if c.Meta == nil {
		c.Meta = &ConfigMeta{
			ConfigVersion: 2,
		}
	}
	c.Meta.ConfigVersion = 2

	// 2. Relations: JunctionTable — can't auto-detect, Validate checks
	// 3. ApprovedTools: the custom UnmarshalJSON handles the legacy format

	// 4. Bump version
	c.Version = 2
}

// normalizeV2ToV3 upgrades v2 → v3 configs.
//
// Changes in v3:
//   - SearchField and QueryParam removed from Endpoint struct (no longer used)
//   - Legacy op="find"/op="list" were previously converted to strategy (grep/filter);
//     с v4 это больше НЕ поддерживается — конвертация убрана, такие эндпоинты
//     остаются как есть и падают в Validate() (op: unsupported).
func (c *Config) normalizeV2ToV3() {
	if c.Meta != nil {
		c.Meta.ConfigVersion = 3
	}
	c.Version = 3
}

// normalizeV3ToV4 upgrades v3 → v4 configs.
//
// Changes in v4:
//   - Legacy op="find"/op="list" endpoints are NO LONGER SUPPORTED: they are
//     left untouched and fail Op.Valid() in Validate(). No conversion, no fallback.
//   - FieldRule.ID introduced: disabled_default_filterable/searchable/enum_rules
//     теперь матчатся по стабильному ID, а не по Reason-префиксу. Legacy
//     Reason-префиксы конвертируются в ID, чтобы существующие конфиги не
//     потеряли отключение правил.
func (c *Config) normalizeV3ToV4() {
	// Legacy Reason-префиксы → стабильные ID (см. FieldRule.ID в types.go).
	legacyReasonToID := map[string]string{
		"Common filterable":      "filterable.common",
		"Image/SEO":              "searchable.block_image",
		"Columns that typically": "enum.contains",
	}
	convert := func(disabled []string) []string {
		out := make([]string, 0, len(disabled))
		for _, d := range disabled {
			if id, ok := legacyReasonToID[d]; ok {
				out = append(out, id)
			} else {
				out = append(out, d) // кастомный/неизвестный — оставляем как есть
			}
		}
		return out
	}
	c.DisabledDefaultFilterableRules = convert(c.DisabledDefaultFilterableRules)
	c.DisabledDefaultSearchableRules = convert(c.DisabledDefaultSearchableRules)
	c.DisabledDefaultEnumRules = convert(c.DisabledDefaultEnumRules)

	if c.Meta != nil {
		c.Meta.ConfigVersion = 4
	}
	c.Version = 4
}

// normalizeV0 handles version-0 configs (pre-schema-version configs).
// Currently a no-op because v0 → v1 was already handled by Validate side-effect.
//
//nolint:unused
func (c *Config) normalizeV0() {
	c.Version = 1
}

// ConfigMeta carries metadata about the config itself.
// Unlike Version (schema version), these describe when/how the config was created.
type ConfigMeta struct {
	// ConfigVersion is the schema version of this config.
	ConfigVersion int `json:"config_version"`

	// GeneratedAt is the ISO 8601 timestamp when this config was generated.
	GeneratedAt string `json:"generated_at,omitempty"`

	// GeneratorVersion is the semver tag of helperium that generated this config.
	GeneratorVersion string `json:"generator_version,omitempty"`
}

// ApprovedTool is a structured approval for a write-endpoint.
// Supports both legacy []string format and expanded format with method scoping.
type ApprovedTool struct {
	// Endpoint is the path from endpoints[] (e.g. "/students").
	Endpoint string `json:"endpoint"`

	// Methods restricts which HTTP methods are approved.
	// Empty or nil means ALL methods for this endpoint are approved.
	Methods []HTTPMethod `json:"methods,omitempty"`
}

// UnmarshalJSON implements json.Unmarshaler for backward compatibility.
// Accepts both:
//   - string: legacy format ("/students") → {endpoint: "/students"}
//   - object: {endpoint: "/students", methods: ["POST"]}
func (a *ApprovedTool) UnmarshalJSON(data []byte) error {
	// Try string first (legacy format)
	var s string
	if err := json.Unmarshal(data, &s); err == nil {
		a.Endpoint = s
		a.Methods = nil
		return nil
	}

	// Try object format
	type alias ApprovedTool
	var al alias
	if err := json.Unmarshal(data, &al); err != nil {
		return err
	}
	a.Endpoint = al.Endpoint
	a.Methods = al.Methods
	return nil
}
