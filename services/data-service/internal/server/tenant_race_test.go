package server

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// TestServeHTTP_HoldsReadLockDuringRequest — регресс гонки Config/Router при hot-reload.
//
// Проблема: resolveTenant снимал RLock до использования inst.Router; ReloadTenant
// перезаписывает inst.Router/inst.Config под ts.mu.Lock. Запрос, попавший между
// RUnlock и Router.ServeHTTP, исполнялся на старом/новом роутере непредсказуемо.
//
// Тест: блокирующий роутер + конкурентный ReloadTenant. Пока запрос выполняется,
// ReloadTenant не должен завершиться (RLock удерживается). После release —
// reload проходит.
func TestServeHTTP_HoldsReadLockDuringRequest(t *testing.T) {
	ts := newTestTenantStore(t)

	cfg := newInMemoryConfig(t)
	inst, err := ts.AddTenant(context.Background(), "race-tenant", cfg, "")
	if err != nil {
		t.Fatalf("AddTenant: %v", err)
	}

	// Блокирующий роутер: сигналит о старте, ждёт release.
	started := make(chan struct{})
	release := make(chan struct{})
	blocking := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		close(started)
		<-release
		w.WriteHeader(http.StatusOK)
	})

	// Заменяем роутер на блокирующий (та же гонка: ServeHTTP читает inst.Router).
	ts.mu.Lock()
	inst.Router = blocking
	ts.mu.Unlock()

	// Запрос в горутине.
	req := httptest.NewRequest(http.MethodGet, "/groups/x", nil)
	req.Header.Set("X-Tenant-ID", "race-tenant")
	rec := httptest.NewRecorder()
	reqDone := make(chan struct{})
	go func() {
		ts.ServeHTTP(rec, req)
		close(reqDone)
	}()

	select {
	case <-started:
	case <-time.After(2 * time.Second):
		t.Fatal("request did not reach router")
	}

	// Пишем конфиг для reload на диск.
	cfgPath := filepath.Join(t.TempDir(), "race.json")
	data, err := json.Marshal(cfg)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(cfgPath, data, 0644); err != nil {
		t.Fatal(err)
	}

	// ReloadTenant в горутине — должен ждать, пока запрос держит RLock.
	reloadDone := make(chan error, 1)
	go func() {
		reloadDone <- ts.ReloadTenant(context.Background(), "race-tenant", cfgPath)
	}()

	select {
	case err := <-reloadDone:
		t.Fatalf("ReloadTenant completed while request in flight (err=%v) — RLock not held", err)
	case <-time.After(300 * time.Millisecond):
		// Ожидаемо: reload заблокирован, пока идёт запрос.
	}

	// Отпускаем роутер.
	close(release)
	select {
	case <-reqDone:
	case <-time.After(2 * time.Second):
		t.Fatal("request did not finish after release")
	}

	// Reload теперь должен пройти.
	select {
	case err := <-reloadDone:
		if err != nil {
			t.Fatalf("ReloadTenant after release: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("ReloadTenant did not complete after request finished")
	}
}
