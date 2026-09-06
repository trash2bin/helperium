// Package server provides admin-dashboard HTTP server (widget problem reports).
//
// HTTP routes called (to upstream services):
//
//	reportsListHandler   -> api-service:GET /admin/reports               (list problem reports)
//	reportStatusHandler  -> api-service:POST /admin/reports/{id}/status  (mark reviewed)
package server

import (
	"net/http"
	"net/url"

	"github.com/go-chi/chi/v5"
)

// reportsListHandler proxies GET /api/reports to api-service GET /admin/reports,
// forwarding the limit and status filters. The upstream response (including its
// validation errors) is passed through unchanged.
func (s *Server) reportsListHandler(w http.ResponseWriter, r *http.Request) {
	params := url.Values{}
	if v := r.URL.Query().Get("limit"); v != "" {
		params.Set("limit", v)
	}
	if v := r.URL.Query().Get("status"); v != "" {
		params.Set("status", v)
	}
	path := "/admin/reports"
	if encoded := params.Encode(); encoded != "" {
		path += "?" + encoded
	}
	s.proxyToApiService(w, r, path)
}

// reportStatusHandler proxies POST /api/reports/{reportID}/status to
// api-service POST /admin/reports/{report_id}/status.
func (s *Server) reportStatusHandler(w http.ResponseWriter, r *http.Request) {
	reportID := chi.URLParam(r, "reportID")
	s.proxyToApiService(w, r, "/admin/reports/"+url.PathEscape(reportID)+"/status")
}
