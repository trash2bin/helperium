package handlers

import (
	"fmt"
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
