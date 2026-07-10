package runtime

import (
	"errors"
	"testing"
)

func TestQueryError_Unwrap(t *testing.T) {
	inner := errors.New("inner error")
	qe := &QueryError{Op: "test", Reason: "something", Err: inner}
	if !errors.Is(qe, inner) {
		t.Error("QueryError.Unwrap should unwrap to inner error")
	}
}

func TestQueryError_Unwrap_Nil(t *testing.T) {
	qe := &QueryError{Op: "test", Reason: "something"}
	got := qe.Unwrap()
	if got != nil {
		t.Errorf("QueryError.Unwrap with nil Err = %v, want nil", got)
	}
}

func TestQueryError_Error(t *testing.T) {
	qe := &QueryError{Op: "BuildGetByID", Reason: "entity has empty Table"}
	msg := qe.Error()
	if msg != "runtime: BuildGetByID: entity has empty Table" {
		t.Errorf("QueryError.Error() = %q, want 'runtime: BuildGetByID: entity has empty Table'", msg)
	}
}
