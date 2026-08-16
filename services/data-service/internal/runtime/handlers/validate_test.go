package handlers

import (
	"testing"

	"github.com/trash2bin/helperium/data-service/internal/runtime"
)

func TestParseEntityID(t *testing.T) {
	intEntity := runtime.Entity{
		IDColumn: "id",
		Fields:   []runtime.EntityField{{Name: "id", Column: "id", Type: "int", PrimaryKey: true}},
	}

	id, err := parseEntityID(intEntity, "42")
	if err != nil {
		t.Fatalf("valid integer ID: %v", err)
	}
	if id != int64(42) {
		t.Fatalf("integer ID type/value = %#v, want int64(42)", id)
	}

	if _, err := parseEntityID(intEntity, "AP-100006"); err == nil {
		t.Fatal("non-integer ID for integer primary key must fail validation")
	}

	stringEntity := runtime.Entity{
		IDColumn: "id",
		Fields:   []runtime.EntityField{{Name: "id", Column: "id", Type: "string", PrimaryKey: true}},
	}
	id, err = parseEntityID(stringEntity, "АП-100006")
	if err != nil {
		t.Fatalf("literal string ID: %v", err)
	}
	if id != "АП-100006" {
		t.Fatalf("string ID = %#v, want literal identifier", id)
	}
}
