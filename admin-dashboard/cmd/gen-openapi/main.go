// Quick utility to generate openapi.json at build time.
// Called from build.sh:
//   go run ./cmd/gen-openapi/
package main

import (
	"log"

	"github.com/trash2bin/helperium/admin-dashboard/internal/openapi"
)

func main() {
	spec := openapi.GenerateSpec()
	if err := openapi.WriteSpecToFile(spec, "internal/server/static/openapi.json"); err != nil {
		log.Fatalf("failed to write openapi.json: %v", err)
	}
	log.Println("openapi.json generated")
}
