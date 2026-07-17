// Package server provides the admin-dashboard HTTP server.
package server

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

// AuditEntry — одна запись аудита изменений конфигурации.
type AuditEntry struct {
	Timestamp time.Time `json:"timestamp"`
	ActorRole string    `json:"actor_role"` // admin / viewer
	Action    string    `json:"action"`      // tenant.create / config.update / tool.approve / ...
	Resource  string    `json:"resource"`    // tenant ID, agent name, path summary
	Details   string    `json:"details,omitempty"` // человекочитаемый контекст
}

// AuditStore — append-only store для аудита, ротация по месяцам.
type AuditStore struct {
	mu     sync.Mutex
	dir    string
	file   *os.File
	buffer []AuditEntry // last 10k entries in-memory
}

// NewAuditStore создаёт AuditStore в указанной директории.
func NewAuditStore(dir string) *AuditStore {
	if err := os.MkdirAll(dir, 0755); err != nil {
		slog.Warn("audit: failed to create directory, falling back to temp", "dir", dir, "error", err)
		dir = os.TempDir()
	}
	s := &AuditStore{
		dir:    dir,
		buffer: make([]AuditEntry, 0, 10000),
	}
	s.rotateFile()
	return s
}

func (a *AuditStore) currentPath() string {
	return filepath.Join(a.dir, fmt.Sprintf("admin-audit-%s.jsonl", time.Now().UTC().Format("2006-01")))
}

func (a *AuditStore) rotateFile() {
	path := a.currentPath()
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		slog.Warn("audit: failed to open log file", "path", path, "error", err)
		return
	}
	if a.file != nil {
		a.file.Close()
	}
	a.file = f
}

// Log записывает одну запись аудита. Thread-safe.
func (a *AuditStore) Log(actorRole, action, resource, details string) {
	entry := AuditEntry{
		Timestamp: time.Now().UTC(),
		ActorRole: actorRole,
		Action:    action,
		Resource:  resource,
		Details:   details,
	}
	data, err := json.Marshal(entry)
	if err != nil {
		slog.Warn("audit: failed to marshal entry", "error", err)
		return
	}

	a.mu.Lock()
	defer a.mu.Unlock()

	if a.file != nil {
		if a.file.Name() != a.currentPath() {
			a.rotateFile()
		}
	} else {
		a.rotateFile()
	}

	if a.file != nil {
		if _, err := a.file.Write(data); err != nil {
			slog.Warn("audit: failed to write entry", "error", err)
		}
		a.file.Write([]byte{'\n'})
		a.file.Sync()
	}

	a.buffer = append(a.buffer, entry)
	if len(a.buffer) > 10000 {
		a.buffer = a.buffer[len(a.buffer)-10000:]
	}

	slog.Debug("audit", "actor", actorRole, "action", action, "resource", resource)
}

// Recent возвращает последние N записей (из файла + in-memory буфер).
// Thread-safe.
func (a *AuditStore) Recent(limit int) []AuditEntry {
	a.mu.Lock()
	defer a.mu.Unlock()

	// In-memory буфер содержит самое свежее
	n := len(a.buffer)
	if n >= limit {
		result := make([]AuditEntry, limit)
		copy(result, a.buffer[n-limit:])
		return result
	}

	// Собираем из буфера
	combined := make([]AuditEntry, n)
	copy(combined, a.buffer)

	// Добавляем из файла
	if a.file != nil {
		data, err := os.ReadFile(a.file.Name())
		if err == nil && len(data) > 0 {
			lines := strings.Split(strings.TrimSpace(string(data)), "\n")
			for _, line := range lines {
				if line == "" {
					continue
				}
				var entry AuditEntry
				if json.Unmarshal([]byte(line), &entry) == nil {
					combined = append(combined, entry)
				}
			}
		}
	}

	// Дедупликация по ключу (timestamp+action+resource+details)
	if len(combined) > limit {
		sort.Slice(combined, func(i, j int) bool {
			return combined[i].Timestamp.After(combined[j].Timestamp)
		})
		seen := make(map[string]bool)
		unique := make([]AuditEntry, 0, limit)
		for _, e := range combined {
			key := fmt.Sprintf("%d|%s|%s", e.Timestamp.UnixNano(), e.Action, e.Resource)
			if !seen[key] {
				seen[key] = true
				unique = append(unique, e)
			}
			if len(unique) >= limit {
				break
			}
		}
		return unique
	}

	return combined
}

// ── auditMiddleware — логирует все успешные мутирующие запросы ──

// auditMiddleware возвращает middleware, которая логирует POST/PUT/DELETE на /api/*.
func (s *Server) auditMiddleware() func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			// Wrap ResponseWriter to capture status
			wr := &responseWriter{ResponseWriter: w, statusCode: http.StatusOK}

			// Пропускаем запрос "как есть", audit — после хендлера
			next.ServeHTTP(wr, r)

			// Логируем только успешные мутирующие запросы к /api/
			if wr.statusCode < 200 || wr.statusCode >= 300 {
				return
			}
			if !strings.HasPrefix(r.URL.Path, "/api/") {
				return
			}
			switch r.Method {
			case http.MethodPost, http.MethodPut, http.MethodDelete, http.MethodPatch:
				// audit
			default:
				return
			}

			role := RoleFromContext(r.Context())
			action := auditAction(r.Method, r.URL.Path)
			resource := auditResource(r.URL.Path)

			details := r.Method + " " + r.URL.Path
			if role != "" {
				details = fmt.Sprintf("[%s] %s %s", role, r.Method, r.URL.Path)
			}

			s.auditStore.Log(role, action, resource, details)
		})
	}
}

// ── Path → action mapping ──

// auditAction возвращает человекочитаемое название действия по HTTP-методу и пути.
func auditAction(method, path string) string {
	path = cleanPath(path)
	relativePath := strings.TrimPrefix(path, "/api/")

	for pattern, action := range auditPatterns {
		if matchAuditPattern(relativePath, pattern) {
			return action
		}
	}

	methodVerb := methodVerb(method)
	segments := strings.Split(strings.Trim(relativePath, "/"), "/")
	if len(segments) > 0 {
		last := segments[len(segments)-1]
		// Если последний сегмент — UUID или ID, берём предпоследний
		if isIDLike(last) && len(segments) >= 2 {
			last = segments[len(segments)-2]
		}
		return fmt.Sprintf("%s.%s", last, methodVerb)
	}

	return fmt.Sprintf("%s %s", method, path)
}

// auditResource извлекает имя ресурса (tenant ID, agent name) из пути.
func auditResource(path string) string {
	path = cleanPath(path)
	segments := strings.Split(strings.Trim(path, "/"), "/")
	for i, seg := range segments {
		if seg == "tenants" && i+1 < len(segments) {
			return "tenant:" + segments[i+1]
		}
		if seg == "agents" && i+1 < len(segments) {
			return "agent:" + segments[i+1]
		}
		if (seg == "llm-providers" || seg == "llm_providers") && i+1 < len(segments) {
			return "llm-provider:" + segments[i+1]
		}
	}
	return ""
}

// auditPatterns — маппинг относительных путей к именам действий.
var auditPatterns = map[string]string{
	"tenants":                              "tenant.create",
	"tenants/{id}":                         "tenant.delete",
	"tenants/{id}/config":                  "config.update",
	"tenants/{id}/introspect":              "tenant.introspect",
	"tenants/{id}/tools/{toolName}/approve": "tool.approve",
	"tenants/upload-sqlite":                "tenant.upload",

	"rag/config":                           "rag.config.update",
	"rag/documents/import":                 "rag.doc.import",
	"rag/documents/upload":                 "rag.doc.upload",
	"rag/documents/delete":                 "rag.doc.delete",

	"agents":                               "agent.create",
	"agents/{name}":                        "agent.update",
	"agents/{name}/delete":                 "agent.delete",
	"agents/{name}/abuse":                  "agent.abuse.update",

	"llm-providers":                        "llm-provider.add",
	"llm-providers/{name}":                 "llm-provider.update",
	"llm-providers/{name}/delete":          "llm-provider.delete",
	"llm-providers/{name}/toggle":          "llm-provider.toggle",

	"voice-config":                         "voice-config.update",

	"abuse-settings":                       "abuse-settings.update",
	"abuse-preset/{preset}":                "abuse-preset.set",
	"admin/abuse-config/reload":            "abuse-config.reload",

	"db/test":                              "db.test",
}

// ── Helpers ──

func methodVerb(method string) string {
	switch method {
	case http.MethodPost:
		return "create"
	case http.MethodPut:
		return "update"
	case http.MethodDelete:
		return "delete"
	case http.MethodPatch:
		return "patch"
	default:
		return strings.ToLower(method)
	}
}

func cleanPath(path string) string {
	if idx := strings.Index(path, "?"); idx >= 0 {
		path = path[:idx]
	}
	return path
}

func isIDLike(s string) bool {
	// UUID, числовой ID, короткие хеши
	if len(s) > 30 || len(s) < 8 {
		return false
	}
	for _, c := range s {
		if (c < '0' || c > '9') && (c < 'a' || c > 'f') && c != '-' {
			return false
		}
	}
	return strings.Count(s, "-") <= 5 && strings.Count(s, "-") >= 0
}

func matchAuditPattern(path, pattern string) bool {
	pathSegs := strings.Split(strings.Trim(path, "/"), "/")
	patSegs := strings.Split(strings.Trim(pattern, "/"), "/")

	if len(pathSegs) != len(patSegs) {
		return false
	}

	for i := range patSegs {
		if strings.HasPrefix(patSegs[i], "{") && strings.HasSuffix(patSegs[i], "}") {
			continue
		}
		if patSegs[i] != pathSegs[i] {
			return false
		}
	}
	return true
}
