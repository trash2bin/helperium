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
