package handlers

import (
	"context"
	"net/http/httptest"
	"testing"
	"time"
)

func TestQueryCtx_ReservesResponseMarginBeforeRequestDeadline(t *testing.T) {
	requestCtx, cancelRequest := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancelRequest()
	req := httptest.NewRequest("GET", "/products", nil).WithContext(requestCtx)

	handlerCtx := &Context{QueryTimeout: 30 * time.Second}
	queryCtx, cancelQuery := handlerCtx.queryCtx(req)
	defer cancelQuery()

	requestDeadline, ok := requestCtx.Deadline()
	if !ok {
		t.Fatal("request context should have a deadline")
	}
	queryDeadline, ok := queryCtx.Deadline()
	if !ok {
		t.Fatal("query context should have a deadline")
	}
	if !queryDeadline.Before(requestDeadline) {
		t.Fatalf("query deadline %s should precede request deadline %s", queryDeadline, requestDeadline)
	}

	margin := requestDeadline.Sub(queryDeadline)
	if margin < queryResponseTimeoutMargin-50*time.Millisecond {
		t.Fatalf("query deadline reserved only %s; want approximately %s", margin, queryResponseTimeoutMargin)
	}
}

func TestQueryCtx_UsesConfiguredTimeoutWithoutRequestDeadline(t *testing.T) {
	req := httptest.NewRequest("GET", "/products", nil)
	handlerCtx := &Context{QueryTimeout: 200 * time.Millisecond}

	started := time.Now()
	queryCtx, cancelQuery := handlerCtx.queryCtx(req)
	defer cancelQuery()
	deadline, ok := queryCtx.Deadline()
	if !ok {
		t.Fatal("query context should have a deadline")
	}
	if remaining := time.Until(deadline); remaining < 100*time.Millisecond || deadline.Before(started) {
		t.Fatalf("configured timeout was not applied, remaining=%s", remaining)
	}
}
