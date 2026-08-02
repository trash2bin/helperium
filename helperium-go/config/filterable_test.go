package config

import (
	"testing"
)

func TestFieldRule_AllowNames(t *testing.T) {
	r := FieldRule{AllowNames: []string{"price", "status"}}
	tests := []struct {
		name  string
		match bool
	}{
		{"price", true},
		{"status", true},
		{"quantity", false},
		{"name", false},
	}
	for _, tc := range tests {
		got := r.Matches(tc.name)
		if got != tc.match {
			t.Errorf("FieldRule{AllowNames: [price, status]}.Matches(%q) = %v, want %v", tc.name, got, tc.match)
		}
	}
}

func TestFieldRule_AllowSuffix(t *testing.T) {
	r := FieldRule{AllowSuffix: []string{"_id", "_date"}}
	tests := []struct {
		name  string
		match bool
	}{
		{"brand_id", true},
		{"category_id", true},
		{"created_at", false}, // _at != _date
		{"order_date", true},
	}
	for _, tc := range tests {
		got := r.Matches(tc.name)
		if got != tc.match {
			t.Errorf("FieldRule{AllowSuffix: [_id, _date]}.Matches(%q) = %v, want %v", tc.name, got, tc.match)
		}
	}
}

func TestFieldRule_AllowContains(t *testing.T) {
	r := FieldRule{AllowContains: []string{"status", "type"}}
	if !r.Matches("order_status") {
		t.Error("expected order_status to match AllowContains status")
	}
	if !r.Matches("product_type") {
		t.Error("expected product_type to match AllowContains type")
	}
	if r.Matches("name") {
		t.Error("expected name to NOT match AllowContains status|type")
	}
}

func TestFieldRule_BlockNames(t *testing.T) {
	r := FieldRule{AllowSuffix: []string{"_id"}, BlockNames: []string{"tenant_id"}}
	if r.Matches("tenant_id") {
		t.Error("expected tenant_id to be blocked by BlockNames")
	}
	if !r.Matches("brand_id") {
		t.Error("expected brand_id to pass (allow + no block)")
	}
}

func TestFieldRule_BlockSuffix(t *testing.T) {
	r := FieldRule{BlockSuffix: []string{"_image", "_url"}}
	// Empty allow → allow-all.
	if !r.Matches("name") {
		t.Error("expected name to pass (empty allow = allow-all)")
	}
	// "image" does not end with "_image" — not blocked
	if !r.Matches("image") {
		t.Error("expected 'image' to pass (suffix is '_image', exact 'image' is different)")
	}
	if r.Matches("main_image") {
		t.Error("expected main_image to be blocked by _image suffix")
	}
	if r.Matches("photo_url") {
		t.Error("expected photo_url to be blocked by _url suffix")
	}
}

func TestFieldRule_BlockContains(t *testing.T) {
	r := FieldRule{BlockContains: []string{"json", "seo"}}
	if r.Matches("seo_title") {
		t.Error("expected seo_title to be blocked by BlockContains seo")
	}
	if r.Matches("product_json") {
		t.Error("expected product_json to be blocked by BlockContains json")
	}
	if !r.Matches("name") {
		t.Error("expected name to pass (no block match)")
	}
}

func TestFieldRule_EmptyAllow(t *testing.T) {
	r := FieldRule{BlockNames: []string{"tenant_id"}}
	// Empty allow means allow-all.
	if !r.Matches("name") {
		t.Error("empty allow should mean allow-all")
	}
	if r.Matches("tenant_id") {
		t.Error("expected tenant_id to be blocked")
	}
}

func TestFieldRule_EmptyRule(t *testing.T) {
	r := FieldRule{}
	if !r.Matches("anything") {
		t.Error("empty rule (no allow, no block) should allow everything")
	}
}

func TestIsFilterableField_Implicit(t *testing.T) {
	tests := []struct {
		name  string
		field EntityField
		want  bool
	}{
		{
			name:  "FK fields always filterable",
			field: EntityField{Name: "brand_id", Column: "brand_id", Type: FieldTypeInt},
			want:  true,
		},
		{
			name:  "tenant_id NOT implicitly filterable (security)",
			field: EntityField{Name: "tenant_id", Column: "tenant_id", Type: FieldTypeInt},
			want:  false,
		},
		{
			name:  "business date always filterable",
			field: EntityField{Name: "order_date", Column: "order_date", Type: FieldTypeDatetime},
			want:  true,
		},
		{
			name:  "is_available always filterable",
			field: EntityField{Name: "is_available", Column: "is_available", Type: FieldTypeBool},
			want:  true,
		},
		{
			name:  "is_active always filterable",
			field: EntityField{Name: "is_active", Column: "is_active", Type: FieldTypeBool},
			want:  true,
		},
		{
			name:  "is_popular NOT implicit (marketing noise)",
			field: EntityField{Name: "is_popular", Column: "is_popular", Type: FieldTypeBool},
			want:  false,
		},
		{
			name:  "system date created_at NOT implicit",
			field: EntityField{Name: "created_at", Column: "created_at", Type: FieldTypeDatetime},
			want:  false, // _at != _date
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := IsFilterableField(tc.field, nil)
			if got != tc.want {
				t.Errorf("IsFilterableField(%+v, nil) = %v, want %v", tc.field, got, tc.want)
			}
		})
	}
}

func TestIsFilterableField_WithRules(t *testing.T) {
	rules := []FieldRule{
		{AllowNames: []string{"price", "status", "type", "quantity", "rating"}},
		{AllowSuffix: []string{"_id", "_date"}, BlockNames: []string{"tenant_id"}},
		{AllowContains: []string{"status"}, BlockNames: []string{"seo_status"}},
	}

	tests := []struct {
		name  string
		field EntityField
		want  bool
	}{
		{name: "FK", field: EntityField{Name: "brand_id", Type: FieldTypeInt}, want: true},
		{name: "date", field: EntityField{Name: "delivery_date", Type: FieldTypeDate}, want: true},
		{name: "explicit allowNames", field: EntityField{Name: "price", Type: FieldTypeFloat}, want: true},
		{name: "rating via allowNames", field: EntityField{Name: "rating", Type: FieldTypeFloat}, want: true},
		{name: "order_status via allowContains", field: EntityField{Name: "order_status", Type: FieldTypeString}, want: true},
		{name: "seo_status blocked", field: EntityField{Name: "seo_status", Type: FieldTypeString}, want: false},
		{name: "tenant_id blocked", field: EntityField{Name: "tenant_id", Type: FieldTypeString}, want: false},
		{name: "name not in rules", field: EntityField{Name: "name", Type: FieldTypeString}, want: false},
		{name: "is_popular not in rules", field: EntityField{Name: "is_popular", Type: FieldTypeBool}, want: false},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := IsFilterableField(tc.field, rules)
			if got != tc.want {
				t.Errorf("IsFilterableField(%+v, rules) = %v, want %v", tc.field, got, tc.want)
			}
		})
	}
}

func TestIsFilterableField_ImplicitTakesPriority(t *testing.T) {
	// Implicit rules should work even with empty/restrictive rules.
	field := EntityField{Name: "brand_id", Type: FieldTypeInt}
	rules := []FieldRule{
		{AllowNames: []string{"price", "status"}},
	}
	if !IsFilterableField(field, rules) {
		t.Error("FK brand_id should be filterable even with restrictive rules — implicit takes priority")
	}

	dateField := EntityField{Name: "order_date", Type: FieldTypeDatetime}
	if !IsFilterableField(dateField, rules) {
		t.Error("date order_date should be filterable even with restrictive rules — implicit takes priority")
	}
}

func TestIsFilterableField_NilRules(t *testing.T) {
	field := EntityField{Name: "brand_id", Type: FieldTypeInt}
	if !IsFilterableField(field, nil) {
		t.Error("FK brand_id should be filterable with nil rules")
	}

	field2 := EntityField{Name: "some_unknown_field", Type: FieldTypeString}
	if IsFilterableField(field2, nil) {
		t.Error("unknown field should NOT be filterable with nil rules (no implicit match)")
	}
}
