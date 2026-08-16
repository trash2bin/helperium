package handlers

import (
	"fmt"
	"strconv"

	"github.com/trash2bin/helperium/data-service/internal/runtime"
)

// ValidationError represents a parameter validation failure.
type ValidationError struct {
	Param   string
	Message string
}

func (e *ValidationError) Error() string {
	return fmt.Sprintf("validation error: %s: %s", e.Param, e.Message)
}

// MaxIDLength limits the length of ID path parameters to prevent DoS attacks
// via excessively long strings (e.g. 1MB values).
const MaxIDLength = 100

// ValidateID validates an ID path parameter extracted from the URL.
// Returns a ValidationError if the ID is empty or exceeds MaxIDLength.
func ValidateID(id string) error {
	if id == "" {
		return &ValidationError{Param: "id", Message: "id is required"}
	}
	if len(id) > MaxIDLength {
		return &ValidationError{
			Param:   "id",
			Message: fmt.Sprintf("id exceeds maximum length (%d characters)", MaxIDLength),
		}
	}
	return nil
}

// parseEntityID validates an ID and converts it to the configured primary-key
// type before the value reaches the database driver. String identifiers remain
// literal; integer identifiers reject non-numeric values with a client error.
func parseEntityID(entity runtime.Entity, id string) (any, error) {
	if err := ValidateID(id); err != nil {
		return nil, err
	}

	for _, field := range entity.Fields {
		if field.Column != entity.IDColumn {
			continue
		}
		if field.Type != "int" {
			return id, nil
		}

		parsed, err := strconv.ParseInt(id, 10, 64)
		if err != nil {
			return nil, &ValidationError{Param: "id", Message: "must be an integer"}
		}
		return parsed, nil
	}

	// Preserve the existing generic behavior when entity metadata is incomplete.
	// BuildGetByID will still reject an entity without an ID column.
	return id, nil
}
