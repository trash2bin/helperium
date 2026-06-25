// schema-gen генерирует JSON Schema из Go-моделей и записывает в specs/schemas/.
//
// Запуск:
//
//	go run ./cmd/schema-gen/
//
// Или через go generate (из data-service/internal/models/):
//
//	go generate ./...
package main

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"

	"github.com/agent-tutor/data-service/internal/models"
	"github.com/invopop/jsonschema"
)

// schemaMap — какие Go-типы в какие файлы писать.
var schemaMap = map[string]any{
	"student.schema.json":        &models.Student{},
	"teacher.schema.json":        &models.Teacher{},
	"discipline.schema.json":     &models.Discipline{},
	"grade.schema.json":          &models.Grade{},
	"schedule-entry.schema.json": &models.ScheduleEntry{},
	"lesson.schema.json":         &models.Lesson{},
}

func main() {
	// Ищем specs/schemas/ относительно data-service/
	// Запуск: cd data-service && go run ./cmd/schema-gen/
	cwd, _ := os.Getwd()
	projectRoot := filepath.Dir(cwd) // data-service/ → project root
	schemasDir := filepath.Join(projectRoot, "specs", "schemas")

	reflector := &jsonschema.Reflector{
		BaseSchemaID:               "https://agent-tutor/schemas",
		RequiredFromJSONSchemaTags: true,
		DoNotReference:             true,
	}

	for filename, model := range schemaMap {
		schema := reflector.Reflect(model)

		outPath := filepath.Join(schemasDir, filename)
		data, err := json.MarshalIndent(schema, "", "  ")
		if err != nil {
			log.Fatalf("marshal %s: %v", filename, err)
		}

		if err := os.WriteFile(outPath, append(data, '\n'), 0644); err != nil {
			log.Fatalf("write %s: %v", filename, err)
		}
		fmt.Printf("  wrote %s (%d bytes)\n", filename, len(data))
	}
	fmt.Println("done.")
}
