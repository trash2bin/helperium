package handlers

import (
	"database/sql"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	_ "modernc.org/sqlite"

	"github.com/trash2bin/helperium/data-service/internal/runtime"
)

// ── Фаза 2: /q/* диспетчер ──────────────────────────────────────────────

// TestQSearch_UnknownEntity_404 проверяет, что /q/* с неизвестным entity
// возвращает 404 (не 500) — whitelist-граница через EntityResolver.
func TestQSearch_UnknownEntity_404(t *testing.T) {
	ctx, _ := newQTestCtx(t)

	h := QSearchHandler(ctx, func(n string) (string, bool) { return "", false },
		func(n string) http.HandlerFunc {
			return func(w http.ResponseWriter, r *http.Request) {
				t.Fatal("handler must not be called for unknown entity")
			}
		})

	req := httptest.NewRequest(http.MethodGet, "/q/search?entity=ghost&pattern=x", nil)
	w := httptest.NewRecorder()
	h(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 for unknown entity, got %d: %s", w.Code, w.Body.String())
	}
}

// TestQSearch_MissingEntity_400 проверяет, что /q/* без entity → 400.
func TestQSearch_MissingEntity_400(t *testing.T) {
	ctx, _ := newQTestCtx(t)

	h := QSearchHandler(ctx, func(n string) (string, bool) { return n, true },
		func(n string) http.HandlerFunc {
			return func(w http.ResponseWriter, r *http.Request) {
				t.Fatal("handler must not be called without entity")
			}
		})

	req := httptest.NewRequest(http.MethodGet, "/q/search?pattern=x", nil)
	w := httptest.NewRecorder()
	h(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for missing entity, got %d: %s", w.Code, w.Body.String())
	}
}

// TestQSearch_StripsEntityParam проверяет, что entity стрипается из query
// перед делегированием (стратегия не должна видеть entity как поле).
func TestQSearch_StripsEntityParam(t *testing.T) {
	ctx, _ := newQTestCtx(t)

	var capturedQuery string
	h := QSearchHandler(ctx, func(n string) (string, bool) { return n, true },
		func(n string) http.HandlerFunc {
			return func(w http.ResponseWriter, r *http.Request) {
				capturedQuery = r.URL.RawQuery
				RespondJSON(w, http.StatusOK, map[string]string{"ok": "true"})
			}
		})

	req := httptest.NewRequest(http.MethodGet, "/q/search?entity=products&pattern=blue", nil)
	w := httptest.NewRecorder()
	h(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
	if strings.Contains(capturedQuery, "entity") {
		t.Errorf("entity must be stripped from query before delegation, got raw query: %q", capturedQuery)
	}
	if !strings.Contains(capturedQuery, "pattern=blue") {
		t.Errorf("non-entity params must be preserved, got raw query: %q", capturedQuery)
	}
}

// TestQGet_UnknownEntity_404 — db_get тоже 404 на неизвестный entity.
func TestQGet_UnknownEntity_404(t *testing.T) {
	ctx, _ := newQTestCtx(t)

	h := QGetHandler(ctx, func(n string) (string, bool) { return "", false },
		func(n string) http.HandlerFunc {
			return func(w http.ResponseWriter, r *http.Request) {
				t.Fatal("handler must not be called for unknown entity")
			}
		})

	req := httptest.NewRequest(http.MethodGet, "/q/get?entity=ghost&id=1", nil)
	w := httptest.NewRecorder()
	h(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 for unknown entity in db_get, got %d", w.Code)
	}
}

// ── Фаза 2.5 fix: /q/* принимает display-имена ────────────────────────
//
// Бенч: модель шлёт entity="Brand" (display из db_map) → 404 unknown_entity.
// Фикс: entityResolver резолвит display-имя в canonical (CanonicalEntityName).
// Здесь тестируем, что make* колбэки получают canonical имя, а не display.
func TestQSearch_ResolvesDisplayNameToCanonical(t *testing.T) {
	ctx, _ := newQTestCtx(t)

	// Резолвер имитирует CanonicalEntityName: "Brand" → "catalog_brand".
	resolve := func(n string) (string, bool) {
		switch n {
		case "Brand", "brand", "Brand (catalog_brand)", "catalog_brand":
			return "catalog_brand", true
		}
		return "", false
	}

	var gotEntity string
	h := QSearchHandler(ctx, resolve,
		func(n string) http.HandlerFunc {
			return func(w http.ResponseWriter, r *http.Request) {
				gotEntity = n
				RespondJSON(w, http.StatusOK, map[string]string{"ok": "true"})
			}
		})

	// Модель прислала display-имя (первый токен db_map).
	req := httptest.NewRequest(http.MethodGet, "/q/search?entity=Brand&pattern=Bosch", nil)
	w := httptest.NewRecorder()
	h(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200 for display name, got %d: %s", w.Code, w.Body.String())
	}
	// ВАЖНО: handler получил уже РАЗРЕШЁННОЕ canonical имя (не "Brand").
	if gotEntity != "catalog_brand" {
		t.Errorf("makeSearch handler must receive canonical entity, got %q", gotEntity)
	}
}

// TestQSearch_CanonicalStillWorks — canonical имя по-прежнему проходит.
func TestQSearch_CanonicalStillWorks(t *testing.T) {
	ctx, _ := newQTestCtx(t)

	resolve := func(n string) (string, bool) {
		if n == "catalog_brand" || n == "Brand" {
			return "catalog_brand", true
		}
		return "", false
	}

	var gotEntity string
	h := QSearchHandler(ctx, resolve,
		func(n string) http.HandlerFunc {
			return func(w http.ResponseWriter, r *http.Request) {
				gotEntity = n
				RespondJSON(w, http.StatusOK, map[string]string{"ok": "true"})
			}
		})

	req := httptest.NewRequest(http.MethodGet, "/q/search?entity=catalog_brand&pattern=Bosch", nil)
	w := httptest.NewRecorder()
	h(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200 for canonical name, got %d", w.Code)
	}
	if gotEntity != "catalog_brand" {
		t.Errorf("makeSearch handler must receive canonical entity, got %q", gotEntity)
	}
}

// newQTestCtx строит минимальный Context для /q/* тестов.
func newQTestCtx(t *testing.T) (*Context, *sql.DB) {
	t.Helper()
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() }) //nolint:errcheck

	adapter := &testStrategyAdapter{db: db}
	builder := runtime.NewBuilder(adapter)

	runtimeEntity := runtime.Entity{
		Name:     "product",
		Table:    "products",
		IDColumn: "id",
		Fields: []runtime.EntityField{
			{Name: "id", Column: "id", Type: "int", PrimaryKey: true},
			{Name: "name", Column: "name", Type: "string"},
		},
	}
	resolver, err := runtime.NewEntityResolver([]runtime.Entity{runtimeEntity})
	if err != nil {
		t.Fatal(err)
	}

	return &Context{
		DB:       adapter,
		Adapter:  adapter,
		Builder:  builder,
		Resolver: resolver,
		URLParam: func(r *http.Request, name string) string { return "" },
	}, db
}
