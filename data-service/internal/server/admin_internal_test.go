package server

import (
	"testing"

	"github.com/trash2bin/helperium/helperium-go/config"
)

func TestDeriveToolName(t *testing.T) {
	tests := []struct {
		name string
		ep   config.Endpoint
		want string
	}{
		{"health", config.Endpoint{Op: config.OpBuiltinHealth}, "health"},
		{"stats", config.Endpoint{Op: config.OpBuiltinStats}, "stats"},
		{"get_by_id", config.Endpoint{Op: config.OpGetByID, Entity: "student"}, "get_student"},
		{"find", config.Endpoint{Op: config.OpFind, Entity: "teacher"}, "find_teacher"},
		{"list", config.Endpoint{Op: config.OpList, Entity: "course"}, "list_course"},
		{"custom_query with id", config.Endpoint{Op: config.OpCustomQuery, QueryID: "active_students", Path: "/custom/active"}, "query_active_students"},
		{"custom_query no id", config.Endpoint{Op: config.OpCustomQuery, Path: "/custom/{id}"}, "query_custom/id"},
		{"unknown op", config.Endpoint{Op: "unknown"}, ""},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := deriveToolName(tc.ep)
			if got != tc.want {
				t.Errorf("deriveToolName(%+v) = %q, want %q", tc.ep, got, tc.want)
			}
		})
	}
}

func TestDeriveToolNames(t *testing.T) {
	endpoints := []config.Endpoint{
		{Path: "/health", Op: config.OpBuiltinHealth},
		{Path: "/students/{id}", Op: config.OpGetByID, Entity: "student"},
		{Path: "/custom/stats", Op: config.OpCustomQuery, QueryID: "stats"},
	}
	names := deriveToolNames(endpoints)
	if len(names) != 3 {
		t.Errorf("deriveToolNames returned %d entries, want 3", len(names))
	}
	// Check by endpoint path
	for _, ep := range endpoints {
		name := names[ep.Path]
		if name == "" {
			t.Errorf("deriveToolNames missing entry for path %q", ep.Path)
		}
	}
}

func TestIsWriteMethod(t *testing.T) {
	writes := []config.HTTPMethod{config.MethodPOST, config.MethodPUT, config.MethodPATCH, config.MethodDELETE}
	reads := []config.HTTPMethod{config.MethodGET}

	for _, m := range writes {
		if !isWriteMethod(m) {
			t.Errorf("isWriteMethod(%q) = false, want true", m)
		}
	}
	for _, m := range reads {
		if isWriteMethod(m) {
			t.Errorf("isWriteMethod(%q) = true, want false", m)
		}
	}
}
