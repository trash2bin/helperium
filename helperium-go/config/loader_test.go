package config_test

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/trash2bin/helperium/helperium-go/config"
)

// specPath возвращает абсолютный путь к файлу в specs/.
// Тесты запускаются из helperium-go/ или helperium-go/config/.
func specPath(t *testing.T, name string) string {
	t.Helper()
	wd, err := os.Getwd()
	if err != nil {
		t.Fatalf("os.Getwd: %v", err)
	}
	candidates := []string{
		filepath.Join(wd, "..", "specs", name),      // из helperium-go/config/ → specs/
		filepath.Join(wd, "..", "..", "specs", name), // из helperium-go/ → repo/specs
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

// fakeGetenv — реализация getenv для Envsubst на основе map.
func fakeGetenv(m map[string]string) func(string) (string, bool) {
	return func(key string) (string, bool) {
		v, ok := m[key]
		return v, ok
	}
}

// TestLoad_GoodConfig — проверяет что config.example.json загружается
// и парсится в ожидаемую форму.
func TestLoad_GoodConfig(t *testing.T) {
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

	// Endpoints: 17 в example (10 entity endpoints + 2 builtin + 5 custom_query).
	if got, want := len(cfg.Endpoints), 17; got != want {
		t.Errorf("len(Endpoints) = %d, want %d", got, want)
	}

	// CustomQueries: 6.
	if got, want := len(cfg.CustomQueries), 6; got != want {
		t.Errorf("len(CustomQueries) = %d, want %d", got, want)
	}

	// MCPTools: 0 — в example их нет.
	if got, want := len(cfg.MCPTools), 0; got != want {
		t.Errorf("len(MCPTools) = %d, want %d", got, want)
	}

	// Stats: 6 counters (по одному на каждую entity).
	if cfg.Stats == nil {
		t.Errorf("Stats is nil")
	} else if got, want := len(cfg.Stats.Counters), 6; got != want {
		t.Errorf("len(Stats.Counters) = %d, want %d", got, want)
	}

	// Auth: nil (в example нет секции auth).
	if cfg.Auth != nil {
		t.Errorf("Auth = %+v, want nil", cfg.Auth)
	}
}

// TestLoad_FileNotFound — Load на несуществующем файле возвращает ошибку.
func TestLoad_FileNotFound(t *testing.T) {
	_, err := config.Load("nonexistent_config_xyz.json")
	if err == nil {
		t.Fatalf("expected error, got nil")
	}
	if !strings.Contains(err.Error(), "no such file") {
		t.Errorf("error = %q, want substring %q", err.Error(), "no such file")
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
	getenv := fakeGetenv(map[string]string{})
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

// TestValidate_InvalidConfig — пустой JSON без version — валиден (version по умолчанию 1).
func TestValidate_InvalidConfig(t *testing.T) {
	raw := []byte(`{"data_source": {"driver": "sqlite", "dsn": "x"}}`) // нет version — ок, default 1
	err := config.Validate(raw)
	if err != nil {
		t.Errorf("Validate(simple config): %v", err)
	}
}

// TestValidate_GoodConfig — config.example.json валиден.
func TestValidate_GoodConfig(t *testing.T) {
	raw, err := os.ReadFile(specPath(t, "config.example.json"))
	if err != nil {
		t.Fatalf("ReadFile: %v", err)
	}
	if err := config.Validate(raw); err != nil {
		t.Errorf("Validate(good config): %v", err)
	}
}

// TestValidate_InvalidJSON — синтаксически битый JSON — ошибка.
func TestValidate_InvalidJSON(t *testing.T) {
	err := config.Validate([]byte(`{not valid json`))
	if err == nil {
		t.Fatalf("expected error, got nil")
	}
	if !strings.Contains(err.Error(), "invalid JSON") {
		t.Errorf("error = %q, want substring 'invalid JSON'", err.Error())
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
	tmp := t.TempDir()
	path := filepath.Join(tmp, "bad.json")
	if err := os.WriteFile(path, []byte(`{not valid json`), 0o600); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}
	_, err := config.Load(path)
	if err == nil {
		t.Fatalf("expected error, got nil")
	}
	// json.Unmarshal ошибка содержит "invalid character" для битого JSON.
	if !strings.Contains(err.Error(), "parse") && !strings.Contains(err.Error(), "invalid character") {
		t.Errorf("error = %q, want substring 'parse'", err.Error())
	}
}

// TestLoad_MissingRequired — загрузка пустого объекта ловится валидацией (требует data_source).
func TestLoad_MissingRequired(t *testing.T) {
	tmp := t.TempDir()
	path := filepath.Join(tmp, "empty.json")
	if err := os.WriteFile(path, []byte(`{}`), 0o600); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}
	_, err := config.Load(path)
	if err == nil {
		t.Fatalf("expected error, got nil")
	}
	// version по умолчанию 1 — ошибка должна быть на data_source
	if !strings.Contains(err.Error(), "data_source.driver") {
		t.Errorf("error = %q, want substring 'data_source.driver'", err.Error())
	}
}
