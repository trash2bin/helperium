package search

import (
	"testing"

	"github.com/trash2bin/helperium/data-service/internal/query"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// =============================================================================
// Tests: SimpleStrategy — ToolDescription, ToolParams, EntityCols
// =============================================================================

func TestSimple_EntityIDCol(t *testing.T) {
	s := NewSimpleStrategy("id", "name", "name")
	if got := s.EntityIDCol(); got != "id" {
		t.Errorf("EntityIDCol() = %q, want %q", got, "id")
	}
}

func TestSimple_EntityNameCol(t *testing.T) {
	s := NewSimpleStrategy("id", "name", "name")
	if got := s.EntityNameCol(); got != "name" {
		t.Errorf("EntityNameCol() = %q, want %q", got, "name")
	}
}

func TestSimple_ToolDescription(t *testing.T) {
	s := NewSimpleStrategy("id", "name", "name")
	desc := s.ToolDescription(sampleEntity)
	if desc == "" {
		t.Error("ToolDescription should not be empty")
	}
	if !contains(desc, "products") {
		t.Errorf("ToolDescription should contain entity name, got: %q", desc)
	}
}

func TestSimple_ToolParams(t *testing.T) {
	s := NewSimpleStrategy("id", "name", "name")
	params := s.ToolParams(sampleEntity)

	// Should have params for: searchField (name), non-PK fields, limit, offset, sort_by
	paramNames := make(map[string]bool)
	for _, p := range params {
		paramNames[p.Name] = true
	}

	expected := []string{"name", "description", "price", "active", "category", "limit", "offset", "sort_by"}
	for _, name := range expected {
		if !paramNames[name] {
			t.Errorf("Missing param: %s", name)
		}
	}

	// Should NOT have id (PK) in params
	if paramNames["id"] {
		t.Error("Unexpected param: id (PK) should not be in ToolParams")
	}

	// Verify limit default description mentions 50
	var limitParam *config.EndpointParam
	for _, p := range params {
		if p.Name == "limit" {
			limitParam = &p
			break
		}
	}
	if limitParam != nil {
		if limitParam.Description == "" {
			t.Error("limit param should have description")
		}
	}
}

func TestSimple_UnknownFieldSkipped(t *testing.T) {
	s := NewSimpleStrategy("id", "name", "name")
	// Unknown field should be silently ignored.
	r := makeRequest(map[string]string{"name": "test", "bogus": "x"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}
	if plan == nil {
		t.Fatal("Expected non-nil plan")
	}
	// Should have exactly 1 condition (name LIKE, not bogus)
	if len(plan.Where) != 1 {
		t.Errorf("Expected 1 condition, got %d", len(plan.Where))
	}
}

func TestSimple_SearchFieldNotFound(t *testing.T) {
	s := NewSimpleStrategy("id", "name", "nonexistent")
	// Search field doesn't exist in entity — should still work (no LIKE condition)
	r := makeRequest(map[string]string{"category": "Electronics"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}
	if plan == nil {
		t.Fatal("Expected non-nil plan")
	}
	if len(plan.Where) != 1 {
		t.Errorf("Expected 1 condition (category = ?), got %d", len(plan.Where))
	}
}

// TestSimple_NegativeLimit directly tests parseLimitParam for the simple default.
func TestSimple_NegativeLimit(t *testing.T) {
	s := NewSimpleStrategy("id", "name", "name")
	r := makeRequest(map[string]string{"name": "test", "limit": "-5"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}
	if plan == nil {
		t.Fatal("Expected non-nil plan")
	}
	if plan.Limit != 50 {
		t.Errorf("Negative limit should fall back to default (50), got %d", plan.Limit)
	}
}

func TestSimple_FormatFull(t *testing.T) {
	s := NewSimpleStrategy("id", "name", "name")
	r := makeRequest(map[string]string{"category": "x", "format": "full"})
	plan, err := s.ParseRequest(r, sampleEntity, testAdapter{})
	if err != nil {
		t.Fatalf("ParseRequest: unexpected error: %v", err)
	}
	if plan.Format != query.FormatFull {
		t.Errorf("Format = %d, want FormatFull (%d)", plan.Format, query.FormatFull)
	}
}
