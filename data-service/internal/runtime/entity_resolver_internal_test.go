package runtime

import (
	"testing"
)

func TestResolve_Found(t *testing.T) {
	entities := []Entity{
		{Name: "customer", Table: "customers", IDColumn: "id"},
		{Name: "order", Table: "orders", IDColumn: "id"},
	}
	resolver, err := NewEntityResolver(entities)
	if err != nil {
		t.Fatalf("NewEntityResolver: %v", err)
	}

	e, ok := resolver.Resolve("customer")
	if !ok {
		t.Fatal("Resolve('customer'): expected ok=true")
	}
	if e.Name != "customer" || e.Table != "customers" {
		t.Errorf("Resolve('customer') = %+v, want name=customer table=customers", e)
	}
}

func TestResolve_NotFound(t *testing.T) {
	resolver, err := NewEntityResolver([]Entity{
		{Name: "customer", Table: "customers"},
	})
	if err != nil {
		t.Fatalf("NewEntityResolver: %v", err)
	}

	_, ok := resolver.Resolve("nonexistent")
	if ok {
		t.Error("Resolve('nonexistent'): expected ok=false")
	}
}

func TestResolve_EmptyResolver(t *testing.T) {
	resolver, err := NewEntityResolver([]Entity{})
	if err != nil {
		t.Fatalf("NewEntityResolver: %v", err)
	}

	_, ok := resolver.Resolve("anything")
	if ok {
		t.Error("Resolve on empty resolver: expected ok=false")
	}
}

func TestColumnFor_Found_Resolved(t *testing.T) {
	entities := []Entity{
		{
			Name: "customer",
			Fields: []EntityField{
				{Name: "id", Column: "identifier"},
				{Name: "email", Column: "email_address"},
			},
		},
	}
	resolver, err := NewEntityResolver(entities)
	if err != nil {
		t.Fatalf("NewEntityResolver: %v", err)
	}

	col, ok := resolver.ColumnFor(entities[0], "email")
	if !ok {
		t.Fatal("ColumnFor('email'): expected ok=true")
	}
	if col != "email_address" {
		t.Errorf("ColumnFor('email') = %q, want %q", col, "email_address")
	}
}

func TestColumnFor_NotFound_Resolved(t *testing.T) {
	resolver, err := NewEntityResolver([]Entity{
		{Name: "customer", Fields: []EntityField{{Name: "id", Column: "id"}}},
	})
	if err != nil {
		t.Fatalf("NewEntityResolver: %v", err)
	}

	_, ok := resolver.ColumnFor(resolver.entities["customer"], "nope")
	if ok {
		t.Error("ColumnFor('nope'): expected ok=false")
	}
}

func TestPublicFor_Found(t *testing.T) {
	entities := []Entity{
		{
			Name: "customer",
			Fields: []EntityField{
				{Name: "id", Column: "identifier"},
				{Name: "email", Column: "email_address"},
			},
		},
	}
	resolver, err := NewEntityResolver(entities)
	if err != nil {
		t.Fatalf("NewEntityResolver: %v", err)
	}

	name, ok := resolver.PublicFor(entities[0], "email_address")
	if !ok {
		t.Fatal("PublicFor('email_address'): expected ok=true")
	}
	if name != "email" {
		t.Errorf("PublicFor('email_address') = %q, want %q", name, "email")
	}
}

func TestPublicFor_NotFound(t *testing.T) {
	resolver, err := NewEntityResolver([]Entity{
		{Name: "customer", Fields: []EntityField{{Name: "id", Column: "id"}}},
	})
	if err != nil {
		t.Fatalf("NewEntityResolver: %v", err)
	}

	_, ok := resolver.PublicFor(resolver.entities["customer"], "nope")
	if ok {
		t.Error("PublicFor('nope'): expected ok=false")
	}
}

func TestAllEntities_List(t *testing.T) {
	entities := []Entity{
		{Name: "z_last", Table: "z"},
		{Name: "a_first", Table: "a"},
		{Name: "m_mid", Table: "m"},
	}
	resolver, err := NewEntityResolver(entities)
	if err != nil {
		t.Fatalf("NewEntityResolver: %v", err)
	}

	names := resolver.AllEntities()
	if len(names) != 3 {
		t.Errorf("AllEntities() length = %d, want 3", len(names))
	}

	m := make(map[string]bool, len(names))
	for _, n := range names {
		m[n] = true
	}
	for _, e := range entities {
		if !m[e.Name] {
			t.Errorf("AllEntities() missing %q", e.Name)
		}
	}
}
