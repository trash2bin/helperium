package config_test

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/trash2bin/helperium/helperium-go/config"
)

// specPath возвращает абсолютный путь к файлу в specs/.
// wd тестов — data-service/internal/config/ (cwd go test), поэтому
// ищем относительно cwd и поднимаемся на нужную глубину.
func specPath(t *testing.T, name string) string {
	t.Helper()
	wd, err := os.Getwd()
	if err != nil {
		t.Fatalf("os.Getwd: %v", err)
	}
	candidates := []string{
		filepath.Join(wd, "..", "..", "specs", name), // helperium-go/config → repo/specs
		filepath.Join(wd, "..", "..", "..", "specs", name), // на случай запуска из helperium-go/
	}
	for _, c := range candidates {
		if _, err := os.Stat(c); err == nil {
			abs, _ := filepath.Abs(c)
			return abs
		}
	}
	t.Fatalf("%s not found; tried %v", name, candidates)
	return ""
}

// withConfigSchema — helper: установить CONFIG_SCHEMA на время теста
// и вернуть cleanup.
func withConfigSchema(t *testing.T, path string) {
	t.Helper()
	prev := os.Getenv("CONFIG_SCHEMA")
	if err := os.Setenv("CONFIG_SCHEMA", path); err != nil {
		t.Fatalf("setenv: %v", err)
	}
	t.Cleanup(func() { _ = os.Setenv("CONFIG_SCHEMA", prev) })
}

// TestLoad_GoodConfig — проверяет что config.example.json загружается
// и парсится в ожидаемую форму.
func TestLoad_GoodConfig(t *testing.T) {
	withConfigSchema(t, specPath(t, "config.schema.json"))

	cfg, err := config.Load(specPath(t, "config.example.json"))
	if err != nil {
		t.Fatalf("Load: %v", err)
	}

	if cfg.Version != 1 {
		t.Errorf("Version = %d, want 1", cfg.Version)
	}

	// DataSource.
	if cfg.DataSource.Driver != config.DriverSQLite {
		t.Errorf("DataSource.Driver = %q, want %q", cfg.DataSource.Driver, config.DriverSQLite)
	}
	if cfg.DataSource.DSN == "" {
		t.Errorf("DataSource.DSN is empty")
	}

	// Entities: groups/student/teacher/discipline/grade/schedule = 6.
	if got, want := len(cfg.Entities), 6; got != want {
		t.Errorf("len(Entities) = %d, want %d", got, want)
	}

	// Endpoints: 12 в example.
	if got, want := len(cfg.Endpoints), 12; got != want {
		t.Errorf("len(Endpoints) = %d, want %d", got, want)
	}

	// CustomQueries: 6.
	if got, want := len(cfg.CustomQueries), 6; got != want {
		t.Errorf("len(CustomQueries) = %d, want %d", got, want)
	}

	// MCPTools: 7.
	if got, want := len(cfg.MCPTools), 7; got != want {
		t.Errorf("len(MCPTools) = %d, want %d", got, want)
	}

	// Stats: 5 counters.
	if cfg.Stats == nil {
		t.Errorf("Stats is nil")
	} else if got, want := len(cfg.Stats.Counters), 5; got != want {
		t.Errorf("len(Stats.Counters) = %d, want %d", got, want)
	}

	// Auth: strategy=none.
	if cfg.Auth == nil {
		t.Errorf("Auth is nil")
	} else if cfg.Auth.Strategy != config.AuthStrategyNone {
		t.Errorf("Auth.Strategy = %q, want %q", cfg.Auth.Strategy, config.AuthStrategyNone)
	}
}

// TestLoad_FileNotFound — Load на несуществующем файле возвращает ошибку.
func TestLoad_FileNotFound(t *testing.T) {
	withConfigSchema(t, specPath(t, "config.schema.json"))

	_, err := config.Load("nonexistent_config_xyz.json")
	if err == nil {
		t.Fatalf("expected error, got nil")
	}
	// os.ReadFile errors include "no such file" on POSIX.
	if !strings.Contains(err.Error(), "no such file") {
		t.Errorf("error = %q, want substring %q", err.Error(), "no such file")
	}
}

// fakeGetenv — реализация getenv для Envsubst на основе map.
func fakeGetenv(m map[string]string) func(string) (string, bool) {
	return func(key string) (string, bool) {
		v, ok := m[key]
		return v, ok
	}
}

// TestEnvsubst_Basic — проверяет обязательную и дефолтную подстановку.
func TestEnvsubst_Basic(t *testing.T) {
	env := map[string]string{
		"HOME": "/home/user",
		"DB":   "test.db",
	}
	getenv := fakeGetenv(env)

	cases := []struct {
		name string
		in   string
		want string
	}{
		{"required present", "${HOME}/data", "/home/user/data"},
		{"with default used", "${MISSING:-fallback}", "fallback"},
		{"required present beats default", "${HOME:-x}", "/home/user"},
		{"multiple vars", "a/${DB}/b/${HOME}/c", "a/test.db/b//home/user/c"},
		{"no vars", "plain string", "plain string"},
		{"empty default", "${MISSING:-}", ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got, err := config.Envsubst(c.in, getenv)
			if err != nil {
				t.Fatalf("Envsubst(%q): %v", c.in, err)
			}
			if got != c.want {
				t.Errorf("Envsubst(%q) = %q, want %q", c.in, got, c.want)
			}
		})
	}
}

// TestEnvsubst_MissingRequired — ${NONEXIST} без default возвращает ошибку.
func TestEnvsubst_MissingRequired(t *testing.T) {
	getenv := fakeGetenv(map[string]string{}) // пустое окружение

	_, err := config.Envsubst("${NONEXIST}", getenv)
	if err == nil {
		t.Fatalf("expected error, got nil")
	}
	if !strings.Contains(err.Error(), "NONEXIST") {
		t.Errorf("error = %q, want substring %q", err.Error(), "NONEXIST")
	}
	if !strings.Contains(err.Error(), "missing required env var") {
		t.Errorf("error = %q, want substring %q", err.Error(), "missing required env var")
	}
}

// TestEnvsubst_Unterminated — ${ без закрывающей } — ошибка.
func TestEnvsubst_Unterminated(t *testing.T) {
	getenv := fakeGetenv(map[string]string{})
	_, err := config.Envsubst("${UNFINISHED", getenv)
	if err == nil {
		t.Fatalf("expected error for unterminated ${")
	}
}

// TestValidate_InvalidConfig — минимальный JSON без version — ошибка
// с указанием поля.
func TestValidate_InvalidConfig(t *testing.T) {
	schemaPath := specPath(t, "config.schema.json")
	raw := []byte(`{"data_source": {"driver": "sqlite", "dsn": "x"}}`) // нет version

	err := config.Validate(raw, schemaPath)
	if err == nil {
		t.Fatalf("expected validation error, got nil")
	}
	// xeipuuv возвращает "(root)" для required-на-корне.
	if !strings.Contains(err.Error(), "version") {
		t.Errorf("error = %q, want substring %q (missing required 'version')", err.Error(), "version")
	}
}

// TestValidate_GoodConfig — config.example.json валиден.
func TestValidate_GoodConfig(t *testing.T) {
	schemaPath := specPath(t, "config.schema.json")
	raw, err := os.ReadFile(specPath(t, "config.example.json"))
	if err != nil {
		t.Fatalf("ReadFile: %v", err)
	}
	if err := config.Validate(raw, schemaPath); err != nil {
		t.Errorf("Validate(good config): %v", err)
	}
}

// TestValidate_SchemaNotFound — путь к несуществующей схеме → ErrSchemaNotFound.
func TestValidate_SchemaNotFound(t *testing.T) {
	err := config.Validate([]byte(`{}`), "/nonexistent/path/config.schema.json")
	if err == nil {
		t.Fatalf("expected error, got nil")
	}
	if !strings.Contains(err.Error(), "config schema not found") {
		t.Errorf("error = %q, want substring %q", err.Error(), "config schema not found")
	}
}

// TestTypes_FieldTypeValid — Valid() для всех FieldType значений.
func TestTypes_FieldTypeValid(t *testing.T) {
	cases := []struct {
		ft   config.FieldType
		want bool
	}{
		{config.FieldTypeString, true},
		{config.FieldTypeInt, true},
		{config.FieldTypeFloat, true},
		{config.FieldTypeBool, true},
		{config.FieldTypeJSON, true},
		{config.FieldTypeDatetime, true},
		{config.FieldTypeDate, true},
		{config.FieldType("weird"), false},
		{config.FieldType(""), false},
	}
	for _, c := range cases {
		if got := c.ft.Valid(); got != c.want {
			t.Errorf("%q.Valid() = %v, want %v", string(c.ft), got, c.want)
		}
	}
}

// TestTypes_DriverValid — Valid() для Driver.
func TestTypes_DriverValid(t *testing.T) {
	if !config.DriverSQLite.Valid() {
		t.Errorf("DriverSQLite.Valid() = false, want true")
	}
	if !config.DriverPostgres.Valid() {
		t.Errorf("DriverPostgres.Valid() = false, want true")
	}
	if config.Driver("mysql").Valid() {
		t.Errorf("Driver(\"mysql\").Valid() = true, want false")
	}
}

// TestTypes_OpValid — Valid() для Op (whitelist из schema).
func TestTypes_OpValid(t *testing.T) {
	for _, ok := range []config.Op{
		config.OpBuiltinHealth, config.OpBuiltinStats,
		config.OpGetByID, config.OpFind, config.OpList, config.OpCustomQuery,
	} {
		if !ok.Valid() {
			t.Errorf("%q.Valid() = false, want true", string(ok))
		}
	}
	if config.Op("magic").Valid() {
		t.Errorf("Op(\"magic\").Valid() = true, want false")
	}
}

// TestTypes_RelationKindValid — Valid() для RelationKind.
func TestTypes_RelationKindValid(t *testing.T) {
	for _, ok := range []config.RelationKind{
		config.RelationManyToOne, config.RelationOneToMany, config.RelationManyToMany,
	} {
		if !ok.Valid() {
			t.Errorf("%q.Valid() = false, want true", string(ok))
		}
	}
	if config.RelationKind("circular").Valid() {
		t.Errorf("RelationKind(\"circular\").Valid() = true, want false")
	}
}

// TestTypes_HTTPMethodValid — Valid() для HTTPMethod.
func TestTypes_HTTPMethodValid(t *testing.T) {
	for _, ok := range []config.HTTPMethod{
		config.MethodGET, config.MethodPOST, config.MethodPUT,
		config.MethodPATCH, config.MethodDELETE,
	} {
		if !ok.Valid() {
			t.Errorf("%q.Valid() = false, want true", string(ok))
		}
	}
	if config.HTTPMethod("BREW").Valid() {
		t.Errorf("HTTPMethod(\"BREW\").Valid() = true, want false")
	}
}

// TestTypes_ParamInValid — Valid() для ParamIn.
func TestTypes_ParamInValid(t *testing.T) {
	for _, ok := range []config.ParamIn{
		config.ParamInPath, config.ParamInQuery, config.ParamInBody,
	} {
		if !ok.Valid() {
			t.Errorf("%q.Valid() = false, want true", string(ok))
		}
	}
	if config.ParamIn("header").Valid() {
		t.Errorf("ParamIn(\"header\").Valid() = true, want false")
	}
}

// TestTypes_ParamTypeValid — Valid() для ParamType.
func TestTypes_ParamTypeValid(t *testing.T) {
	for _, ok := range []config.ParamType{
		config.ParamTypeString, config.ParamTypeInt, config.ParamTypeFloat, config.ParamTypeBool,
	} {
		if !ok.Valid() {
			t.Errorf("%q.Valid() = false, want true", string(ok))
		}
	}
	if config.ParamType("datetime").Valid() {
		t.Errorf("ParamType(\"datetime\").Valid() = true, want false (datetime только в FieldType)")
	}
}

// TestTypes_AuthStrategyValid — Valid() для AuthStrategy.
func TestTypes_AuthStrategyValid(t *testing.T) {
	for _, ok := range []config.AuthStrategy{
		config.AuthStrategyNone, config.AuthStrategyHeader,
	} {
		if !ok.Valid() {
			t.Errorf("%q.Valid() = false, want true", string(ok))
		}
	}
	if config.AuthStrategy("jwt").Valid() {
		t.Errorf("AuthStrategy(\"jwt\").Valid() = true, want false")
	}
}

// TestFileStore_Load — FileStore.Load работает через Load().




// TestConfig_String — smoke-тест String() (используется в логировании).
func TestConfig_String(t *testing.T) {
	c := &config.Config{
		Version: 1,
		DataSource: config.DataSourceConfig{
			Driver: config.DriverSQLite,
		},
	}
	got := c.String()
	if !strings.Contains(got, "version=1") {
		t.Errorf("String() = %q, want substring 'version=1'", got)
	}
	if !strings.Contains(got, "driver=sqlite") {
		t.Errorf("String() = %q, want substring 'driver=sqlite'", got)
	}
}

// TestLoad_BadJSON — синтаксически битый JSON — понятная ошибка.
func TestLoad_BadJSON(t *testing.T) {
	withConfigSchema(t, specPath(t, "config.schema.json"))

	tmp := t.TempDir()
	path := filepath.Join(tmp, "bad.json")
	if err := os.WriteFile(path, []byte(`{not valid json`), 0o600); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}

	_, err := config.Load(path)
	if err == nil {
		t.Fatalf("expected error, got nil")
	}
	if !strings.Contains(err.Error(), "invalid JSON") {
		t.Errorf("error = %q, want substring %q", err.Error(), "invalid JSON")
	}
}

// TestLoad_NilConfig — загрузка пустого объекта ловится JSON Schema (нет required).
func TestLoad_MissingRequired(t *testing.T) {
	withConfigSchema(t, specPath(t, "config.schema.json"))

	tmp := t.TempDir()
	path := filepath.Join(tmp, "empty.json")
	if err := os.WriteFile(path, []byte(`{}`), 0o600); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}

	_, err := config.Load(path)
	if err == nil {
		t.Fatalf("expected error, got nil")
	}
	// Должно ругаться на missing required version или data_source.
	if !strings.Contains(err.Error(), "required") &&
		!strings.Contains(err.Error(), "version") &&
		!strings.Contains(err.Error(), "data_source") {
		t.Errorf("error = %q, want substring mentioning required/version/data_source", err.Error())
	}
}
