package server

import (
	"testing"

	"github.com/trash2bin/helperium/helperium-go/config"
)

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
