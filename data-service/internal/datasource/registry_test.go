package datasource

import (
	"testing"
)

// TestNewRegistry — пустой реестр
func TestNewRegistry(t *testing.T) {
	r := NewRegistry()
	if r == nil {
		t.Fatal("NewRegistry returned nil")
	}
	if len(r.Drivers()) != 0 {
		t.Errorf("expected empty drivers, got %v", r.Drivers())
	}
}

// TestRegisterAndGet — регистрация и получение адаптера
func TestRegisterAndGet(t *testing.T) {
	r := NewRegistry()

	// Регистрируем sqlite
	r.Register(SqliteAdapter{})
	adp, ok := r.Get("sqlite")
	if !ok {
		t.Fatal("expected sqlite adapter to be found")
	}
	if adp.Driver() != "sqlite" {
		t.Errorf("expected driver sqlite, got %s", adp.Driver())
	}

	// Регистрируем postgres
	r.Register(PostgresAdapter{})
	adp, ok = r.Get("postgres")
	if !ok {
		t.Fatal("expected postgres adapter to be found")
	}
	if adp.Driver() != "postgres" {
		t.Errorf("expected driver postgres, got %s", adp.Driver())
	}
}

func TestRegisterAndGet_NotFound(t *testing.T) {
	r := NewRegistry()
	_, ok := r.Get("nonexistent")
	if ok {
		t.Fatal("expected nonexistent adapter to not be found")
	}
}

// TestRegister_PanicOnDuplicate — паника при дубликате
func TestRegister_PanicOnDuplicate(t *testing.T) {
	defer func() {
		if r := recover(); r == nil {
			t.Fatal("expected panic on duplicate registration")
		}
	}()

	r := NewRegistry()
	r.Register(SqliteAdapter{})
	r.Register(SqliteAdapter{}) // should panic
}

// TestNewDefaultRegistry — регистрация всех адаптеров
func TestNewDefaultRegistry(t *testing.T) {
	r := NewDefaultRegistry()
	drivers := r.Drivers()

	// Проверяем, что sqlite и postgres зарегистрированы
	hasSQLite := false
	hasPostgres := false
	for _, d := range drivers {
		switch d {
		case "sqlite":
			hasSQLite = true
		case "postgres":
			hasPostgres = true
		}
	}
	if !hasSQLite {
		t.Error("expected sqlite in default registry")
	}
	if !hasPostgres {
		t.Error("expected postgres in default registry")
	}

	// Проверяем, что можно получить оба
	if _, ok := r.Get("sqlite"); !ok {
		t.Error("expected to get sqlite adapter")
	}
	if _, ok := r.Get("postgres"); !ok {
		t.Error("expected to get postgres adapter")
	}
}

// TestDrivers — список зарегистрированных драйверов
func TestDrivers(t *testing.T) {
	r := NewRegistry()
	if len(r.Drivers()) != 0 {
		t.Errorf("expected 0 drivers, got %d", len(r.Drivers()))
	}

	r.Register(SqliteAdapter{})
	r.Register(PostgresAdapter{})

	drivers := r.Drivers()
	if len(drivers) != 2 {
		t.Errorf("expected 2 drivers, got %d: %v", len(drivers), drivers)
	}
}

// TestRegister_GetAfterMultiple — множетсвенная регистрация
func TestRegister_GetAfterMultiple(t *testing.T) {
	r := NewRegistry()
	r.Register(SqliteAdapter{})

	adp, _ := r.Get("sqlite")
	if adp.QuoteIdentifier("test") != `"test"` {
		t.Errorf("expected quoted identifier, got %s", adp.QuoteIdentifier("test"))
	}
}
