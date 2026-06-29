package runtime

import (
	"encoding/json"
	"testing"
)

func TestCoerceValue(t *testing.T) {
	tests := []struct {
		val, typ string
		want     any
	}{
		// int
		{"42", "int", 42},
		{"0", "int", 0},
		{"notanumber", "int", "notanumber"},

		// float
		{"3.14", "float", 3.14},
		{"0.0", "float", 0.0},
		{"badfloat", "float", "badfloat"},

		// bool
		{"true", "bool", true},
		{"false", "bool", false},
		{"1", "bool", true},
		{"0", "bool", false},
		{"yes", "bool", "yes"},

		// json — массив
		{`["a","b"]`, "json", []any{"a", "b"}},
		// json — объект
		{`{"key":"val"}`, "json", map[string]any{"key": "val"}},
		// json — невалидный
		{`{bad`, "json", `{bad`},

		// string / unknown type
		{"hello", "string", "hello"},
		{"anything", "unknown_type", "anything"},

		// empty
		{"", "int", ""},
		{"", "json", ""},
	}

	for _, tc := range tests {
		got := coerceValue(tc.val, tc.typ)
		// Для JSON сравниваем через перепаковку, потому что want — []any или map[string]any,
		// а got — те же типы. DeepEqual нормально сравнивает.
		wantJSON, _ := json.Marshal(tc.want)
		gotJSON, _ := json.Marshal(got)
		if string(wantJSON) != string(gotJSON) {
			t.Errorf("coerceValue(%q, %q) = %v (%T), want %v (%T)",
				tc.val, tc.typ, got, got, tc.want, tc.want)
		}
	}
}
