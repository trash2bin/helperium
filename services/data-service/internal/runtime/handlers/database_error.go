package handlers

import (
	"context"
	"database/sql"
	"database/sql/driver"
	"errors"
	"net"
	"net/http"
	"strings"
)

const databaseUnavailableMessage = "Tenant database is temporarily unavailable. Please retry shortly."

func respondStrategyDatabaseError(w http.ResponseWriter, err error) {
	if isDatabaseUnavailable(err) {
		RespondError(w, http.StatusServiceUnavailable, "database_unavailable", databaseUnavailableMessage)
		return
	}
	RespondError(w, http.StatusInternalServerError, "db_error",
		"Query execution failed. Check field names via schema tool.")
}

func isDatabaseUnavailable(err error) bool {
	if err == nil {
		return false
	}
	if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled) ||
		errors.Is(err, sql.ErrConnDone) || errors.Is(err, driver.ErrBadConn) {
		return true
	}

	var netErr net.Error
	if errors.As(err, &netErr) && netErr.Timeout() {
		return true
	}

	message := strings.ToLower(err.Error())
	for _, marker := range []string{
		"connection refused",
		"connection reset",
		"connection does not exist",
		"connection is closed",
		"server closed the connection",
		"terminating connection",
		"broken pipe",
		"network is unreachable",
		"no route to host",
		"dial tcp",
		"failed to connect",
		"database is closed",
	} {
		if strings.Contains(message, marker) {
			return true
		}
	}
	return false
}
