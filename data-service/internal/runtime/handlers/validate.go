package handlers

import (
	"fmt"

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

// MaxSearchLength limits the length of search query parameter values.
const MaxSearchLength = 200

// MaxLimit is the maximum allowed value for limit/offset parameters.
const MaxLimit = 10000

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

// ValidateSearchValue validates a search query parameter value.
// Returns a ValidationError if the value exceeds MaxSearchLength.
func ValidateSearchValue(value string) error {
	if len(value) > MaxSearchLength {
		return &ValidationError{
			Param:   "search",
			Message: fmt.Sprintf("search value exceeds maximum length (%d characters)", MaxSearchLength),
		}
	}
	return nil
}

// ValidateEntityField checks that searchField exists in the entity's field definitions.
// Returns a ValidationError if the field is not found.
func ValidateEntityField(entity runtime.Entity, searchField string) error {
	for _, f := range entity.Fields {
		if f.Name == searchField {
			return nil
		}
	}
	return &ValidationError{
		Param:   "searchField",
		Message: fmt.Sprintf("unknown field %q for entity %q", searchField, entity.Name),
	}
}
