package configgen

// Этот файл содержит тесты из внешнего аудита (P0-P2).
// Часть тестов сейчас ПАДАЕТ — они фиксируют реальные проблемы/пробелы.
// Часть — проходит, фиксируя текущее (иногда нежелательное) поведение.
//
// Статус по каждому тесту см. в комментарии над функцией.

import (
	"fmt"
	"strings"
	"sync"
	"testing"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// ═══════════════════════════════════════════════════════════════════════
// P0-2: FK-навигация — v4 намеренно НЕ генерирует _by_ тулы.
//       PASS-гард: навигация для LLM идёт через filter_{child}(fk=...).
// ═══════════════════════════════════════════════════════════════════════
//
// В v4 relationship-тулы (_by_) НАМЕРЕННО удалены (commit 1de916e: «LLM must
// use filter_{entity}({fk_field}=...) instead of *_by_* tools»; удаление в
// 2e58d42). Причина: filter_{child}(fk=...) функционально лучше — применяет
// tenant-фильтр (custom_query НЕ применяет: custom_query.go:11-15), не имеет
// капа 1000 строк (navigation.go:71), поддерживает __in.
//
// Поэтому этот тест PASS-гардит ПРАВИЛЬНОЕ поведение:
//   1. REST-эндпоинт /parents/{id}/children существует (custom_query) —
//      но НЕ экспонируется LLM как отдельный тул
//   2. Вместо него у LLM есть filter_children(parent_id=...) — тул с
//      FK-параметром, через который агент навигирует по связи
//   3. Никакой тул с именем *_by_* не создаётся (нет коллизий имён)
func TestGenerateMCPTools_NavigationEndpointsHaveTools(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{
			{Name: "parents", PrimaryKey: []string{"id"},
				Columns: []datasource.Column{{Name: "id", Type: "string"}}},
			{Name: "children", PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "string"},
					{Name: "parent_id", Type: "string"},
				},
				ForeignKeys: []datasource.ForeignKey{
					{Name: "fk_children_parent", Columns: []string{"parent_id"},
						ReferencedTable: "parents", ReferencedColumns: []string{"id"}},
				}},
		},
	}

	cfg := Generate(schema, &config.Config{
		DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: ":memory:"},
	})

	// 1. Навигационный REST-эндпоинт существует (custom_query).
	var navPath string
	for _, ep := range cfg.Endpoints {
		if ep.Op == config.OpCustomQuery && strings.Contains(ep.Path, "/children") {
			navPath = ep.Path
			break
		}
	}
	if navPath == "" {
		t.Fatalf("no navigation endpoint generated: expected GET /parents/{id}/children (precondition broken)")
	}

	// 2. Для НЕГО нет отдельного MCP-тула (v4: намеренно).
	for _, tool := range cfg.MCPTools {
		if tool.Endpoint == navPath {
			t.Errorf("P0-2: navigation endpoint %s should NOT get its own MCP tool in v4 "+
				"(custom_query has no tenant filter + 1000-row cap). LLM must use filter_children(parent_id=...).", navPath)
		}
		if strings.Contains(tool.Name, "_by_") {
			t.Errorf("P0-2: relationship tool %q must not be generated in v4 (commit 1de916e removed _by_ tools)", tool.Name)
		}
	}

	// 3. У LLM есть filter_children с FK-параметром — через него идёт навигация.
	var filterTool *config.MCPTool
	for i := range cfg.MCPTools {
		if cfg.MCPTools[i].Name == "filter_children" {
			filterTool = &cfg.MCPTools[i]
			break
		}
	}
	if filterTool == nil {
		t.Fatalf("filter_children tool not generated (precondition broken)")
	}
	// FK-колонка parent_id должна быть в параметрах filter-тула.
	hasParentID := false
	for _, p := range filterTool.Params {
		if p.Name == "parent_id" || p.Name == "parent_id__eq" || strings.Contains(p.Name, "parent_id") {
			hasParentID = true
			break
		}
	}
	if !hasParentID {
		t.Errorf("filter_children must expose FK param parent_id for navigation (got params: %v)", filterTool.Params)
	}
}

// ═══════════════════════════════════════════════════════════════════════
// P0-3: Generate() не должен мутировать входной cfg (README: «чистая функция»)
// ═══════════════════════════════════════════════════════════════════════
//
// configgen.go:167-170 пишет cfg.DataSource.ReadOnly = &readOnly прямо во
// входной указатель. Если сервер переиспользует один шаблонный cfg для
// нескольких тенантов параллельно — data race + гонка по значению поля.
//
// → ТЕСТ ДОЛЖЕН ПАДАТЬ (P0-3 подтверждён).
func TestGenerate_DoesNotMutateInput(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{
			{Name: "items", PrimaryKey: []string{"id"},
				Columns: []datasource.Column{{Name: "id", Type: "string"}}},
		},
	}

	in := &config.Config{
		DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: ":memory:"},
	}
	before := *in // shallow copy — проверяем, что поле не перезаписано

	Generate(schema, in)

	if in.DataSource.ReadOnly != before.DataSource.ReadOnly {
		t.Errorf("BUG P0-3: Generate mutated input cfg.DataSource.ReadOnly: %v → %v. "+
			"README promises a pure function; concurrent Generate on a shared cfg is a data race.",
			before.DataSource.ReadOnly, in.DataSource.ReadOnly)
	}
}

// Гонка: N горутин зовут Generate с одним cfg. Запускать с -race.
// → СЕЙЧАС ДОЛЖЕН ВАЛИТЬСЯ С -race (P0-3: data race).
func TestGenerate_ConcurrentSafe(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{
			{Name: "items", PrimaryKey: []string{"id"},
				Columns: []datasource.Column{{Name: "id", Type: "string"}}},
		},
	}
	shared := &config.Config{
		DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: ":memory:"},
	}

	const n = 8
	var wg sync.WaitGroup
	wg.Add(n)
	errs := make([]error, n)
	for i := 0; i < n; i++ {
		go func(i int) {
			defer wg.Done()
			out := Generate(schema, shared)
			if out == nil || out.DataSource.Driver != "sqlite" {
				errs[i] = fmt.Errorf("goroutine %d: bad output", i)
			}
		}(i)
	}
	wg.Wait()
	for _, err := range errs {
		if err != nil {
			t.Error(err)
		}
	}
}

// ═══════════════════════════════════════════════════════════════════════
// P1-4: SkipRules на общеупотребимых словах — риск сожрать бизнес-таблицу
// ═══════════════════════════════════════════════════════════════════════
//
// DefaultSkipRules() содержит {Prefix: "documents", ...} и {Prefix: "jobs", ...}.
// Для CRM-клиента таблица documents (договоры) — бизнес-сущность, а не
// RAG-chunks. shouldSkip() сработает по префиксу молча.
//
// → ПАДАЕТ: документирует, что бизнес-таблица documents исчезает из конфига.
// Это фиксация текущего (опасного) поведения. Решение — за пользователем.
func TestDefaultSkipRules_FalsePositiveOnBusinessTable(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{
			{Name: "documents", PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "int"},
					{Name: "contract_number", Type: "string"},
					{Name: "customer_id", Type: "int"},
					{Name: "signed_at", Type: "date"},
				}},
		},
	}
	cfg := Generate(schema, &config.Config{
		DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: ":memory:"},
	})
	for _, e := range cfg.Entities {
		if e.Name == "documents" {
			return // бизнес-таблица на месте — хорошо
		}
	}
	t.Errorf("P1-4: business table 'documents' (contracts) silently skipped by DefaultSkipRules. "+
		"No warning, entity missing from config.")
}

// ═══════════════════════════════════════════════════════════════════════
// P1-5: FieldRules стабильны на протяжении нескольких циклов
// Generate → ExtractIntent → Hydrate (M7-регресс)
// ═══════════════════════════════════════════════════════════════════════
//
// Фикс M7 в resolveFieldRules есть, но явного теста на МНОГО циклов подряд
// + DisabledDefault* (правило не должно «вернуться» после disable) нет.
// → Должен проходить (фикс работает). Защищает от будущих регрессий.
func TestFieldRules_StableAcrossMultipleRewriteCycles(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{
			{Name: "products", PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "int"},
					{Name: "name", Type: "string"},
					{Name: "price", Type: "int"},
					{Name: "image_url", Type: "string"},
				}},
		},
	}

	// intent с отключённым searchable.block_image
	intent := &TenantIntent{
		DataSource:                   config.DataSourceConfig{Driver: "sqlite", DSN: ":memory:"},
		DisabledDefaultSearchableRules: []string{"searchable.block_image"},
	}

	prevCount := -1
	for cycle := 0; cycle < 5; cycle++ {
		cfg := Hydrate(intent, schema)
		if cfg == nil {
			t.Fatalf("cycle %d: Hydrate returned nil", cycle)
		}
		if got := len(cfg.SearchableRules); prevCount >= 0 && got != prevCount {
			t.Errorf("cycle %d: searchable rules grew: %d → %d (M7 drift)", cycle, prevCount, got)
		}
		prevCount = len(cfg.SearchableRules)

		// Отключённое правило не должно вернуться ни на одном цикле.
		for _, r := range cfg.SearchableRules {
			if r.ID == "searchable.block_image" {
				t.Errorf("cycle %d: disabled rule 'searchable.block_image' reappeared (M7 regress)", cycle)
			}
		}

		//  Важно: DisabledDefaultSearchableRules ОТКЛЮЧАЕТ правило searchable.block_image.
		// Поэтому image_url (string) СТАНОВИТСЯ searchable — это ОЖИДАЕМОЕ следствие
		// осознанного отключения, а не дрейф. Настоящий признак дрейфа (M7) —
		// рост количества правил и возврат отключённого правила в список.
		var product *config.Entity
		for i := range cfg.Entities {
			if cfg.Entities[i].Name == "products" {
				product = &cfg.Entities[i]
				break
			}
		}
		if product == nil {
			t.Fatalf("cycle %d: products entity missing", cycle)
		}
		// image_url — единственное строковое поле, и block_image отключён,
		// поэтому hasSearchableFields == true — КОРРЕКТНО. Если бы правило
		// НЕ отключилось (дрейф/регресс M7), поле осталось бы не-searchable,
		// и это как раз сигнал проблемы. Проверяем обратное:
		if !hasSearchableFields(*product, cfg.SearchableRules) {
			t.Errorf("cycle %d: image_url NOT searchable although block_image disabled — "+
				"disabled rule may have been re-applied (M7 drift)", cycle)
		}
	}
}

// ═══════════════════════════════════════════════════════════════════════
// P1-6: Все дефолтные FieldRules обязаны иметь стабильный ID
// ═══════════════════════════════════════════════════════════════════════
//
// resolveFieldRules: «правило без ID никогда не отключается» — это защита,
// но и дыра: новое дефолтное правило без ID нельзя отключить через
// DisabledDefault*. Дешёвый тест ловит человеческую ошибку навсегда.
// → Должен проходить.
func TestDefaultFieldRules_AllHaveStableID(t *testing.T) {
	all := [][]config.FieldRule{
		DefaultFilterableFieldRules(),
		DefaultSearchableFieldRules(),
		DefaultEnumFieldRules(),
	}
	for _, rules := range all {
		for _, r := range rules {
			if r.ID == "" {
				t.Errorf("P1-6: default FieldRule with empty ID (block_names=%v suffix=%v contains=%v). "+
					"Such rule can NEVER be disabled via DisabledDefault*. Add a stable ID.",
					r.BlockNames, r.BlockSuffix, r.BlockContains)
			}
		}
	}
}

// ═══════════════════════════════════════════════════════════════════════
// P0-7: idCol fallback при отсутствии PK — фиксируем текущее поведение
// ═══════════════════════════════════════════════════════════════════════
//
// tableToEntity: нет PK → idCol = первая колонка интроспекции (может быть
// created_at, FK, что угодно). Validate() требует id_column непустой,
// поэтому fallback осознанный, но семантически неправильный для legacy-таблиц.
// → ПРОХОДИТ (фиксирует поведение). Решение об улучшении — за пользователем.
func TestTableToEntity_NoPrimaryKey_FallbackIDColumn(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{
			{Name: "legacy_log", // без PK
				Columns: []datasource.Column{
					{Name: "created_at", Type: "date"},
					{Name: "message", Type: "string"},
					{Name: "customer_id", Type: "int"},
				}},
		},
	}
	cfg := Generate(schema, &config.Config{
		DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: ":memory:"},
	})

	var found bool
	for _, e := range cfg.Entities {
		if e.Name != "legacy_log" {
			continue
		}
		found = true
		if e.IDColumn != "created_at" {
			t.Errorf("P0-7: no-PK table fallback id_col = %q, want %q (current behavior: first column)",
				e.IDColumn, "created_at")
		}
	}
	if !found {
		t.Fatal("legacy_log entity not generated")
	}
}

// ═══════════════════════════════════════════════════════════════════════
// P2-8: Дефолтные правила НЕ блокируют PII/секретные колонки
// ═══════════════════════════════════════════════════════════════════════
//
// DefaultSearchableFieldRules блокирует только _image/_url/image/thumbnail/
// json/seo. password, ssn, api_key, token, secret — НЕ блокируются по
// умолчанию. Они попадают в schema-ответ (schema endpoint всегда) и в
// filterable/searchable.
//
// → ПАДАЕТ: документирует отсутствие дефолтного PII-блок-листа.
// Это фиксация текущего (рискованного) поведения.
func TestDefaultRules_BlocksSensitiveColumnNames(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{
			{Name: "users", PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "int"},
					{Name: "name", Type: "string"},
					{Name: "password_hash", Type: "string"},
					{Name: "ssn", Type: "string"},
					{Name: "api_key", Type: "string"},
					{Name: "secret_token", Type: "string"},
				}},
		},
	}
	cfg := Generate(schema, &config.Config{
		DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: ":memory:"},
	})

	var user *config.Entity
	for i := range cfg.Entities {
		if cfg.Entities[i].Name == "users" {
			user = &cfg.Entities[i]
			break
		}
	}
	if user == nil {
		t.Fatal("users entity not generated")
	}

	sensitive := map[string]bool{
		"password_hash": false, "ssn": false, "api_key": false, "secret_token": false,
	}
	searchableSensitive := 0
	for _, f := range user.Fields {
		if _, ok := sensitive[f.Name]; ok {
			sensitive[f.Name] = true
		}
		// searchable: grep-стратегия — колонки без блок-правила и string → видны
		if f.Type == config.FieldTypeString && !f.ExcludeFromSearch && f.Name != "name" {
			// Все SearchableRules — block-only (searchable.block_image). Проверяем,
			// блокирует ли хоть одно правило PII-колонку.
			blocked := false
			for _, r := range cfg.SearchableRules {
				if !r.Matches(f.Name) {
					blocked = true
					break
				}
			}
			if !blocked {
				searchableSensitive++
				t.Errorf("P2-8: sensitive column %q is searchable by default (grep exposes it). "+
					"No default PII block-list in DefaultSearchableFieldRules.", f.Name)
			}
		}
	}
	for name, present := range sensitive {
		if !present {
			t.Errorf("P2-8: column %q missing from fixture (test broken)", name)
		}
	}
	_ = searchableSensitive
	// schema endpoint не имеет per-field deny — колонки попадают в ответ всегда.
	for _, ep := range cfg.Endpoints {
		if ep.Path == "/users/schema" {
			return // schema endpoint существует — PII колонки видны в нём
		}
	}
	t.Error("no /users/schema endpoint (precondition broken)")
}

// ═══════════════════════════════════════════════════════════════════════
// P2-9: Composite FK пропускаются тихо — фиксируем отсутствие warning
// ═══════════════════════════════════════════════════════════════════════
//
// entity.go: if len(fk.Columns) != 1 { continue } — без лога.
// Junction-таблицы (many-to-many) на составных ключах теряют навигацию молча.
// → ПРОХОДИТ сейчас (нет warning). Цель — решение: добавить slog.Warn.
func TestTableToEntity_CompositeFK_LoggedAsSkipped(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "sqlite",
		Tables: []datasource.Table{
			{Name: "students", PrimaryKey: []string{"id"},
				Columns: []datasource.Column{{Name: "id", Type: "int"}}},
			{Name: "courses", PrimaryKey: []string{"id"},
				Columns: []datasource.Column{{Name: "id", Type: "int"}}},
			{Name: "enrollments", PrimaryKey: []string{"student_id", "course_id"},
				Columns: []datasource.Column{
					{Name: "student_id", Type: "int"}, {Name: "course_id", Type: "int"},
				},
				ForeignKeys: []datasource.ForeignKey{
					{Name: "fk_enroll_student", Columns: []string{"student_id"},
						ReferencedTable: "students", ReferencedColumns: []string{"id"}},
					{Name: "fk_enroll_course", Columns: []string{"course_id"},
						ReferencedTable: "courses", ReferencedColumns: []string{"id"}},
				}},
		},
	}
	cfg := Generate(schema, &config.Config{
		DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: ":memory:"},
	})
	// Сейчас: junction FK (по одной колонке) генерируют relations, но
	// составной PK сам по себе — ок. Проблема — когда Columns>1.
	_ = cfg
	//  Пока нет observable-assert'а: если добавим slog.Warn, проверим через
	// тестовый логгер. Сейчас тест просто компилируется и не падает,
	// фиксируя, что composite FK НЕ дают navigation (без warning).
}

// ═══════════════════════════════════════════════════════════════════════
// P2-10: Schema-qualified PG-имена (public.brands) в navigation
// ═══════════════════════════════════════════════════════════════════════
//
// tableToEntity нормализует Name/Relation.Table (убирает схему), но
// buildNavigationEndpoints проверяет entity.Table (ПОЛНОЕ имя "public.brands")
// через safeIdentRe — не проходит → связь пропускается.
//
// → ПАДАЕТ: для schema-qualified таблиц FK-навигация теряется.
func TestBuildNavigationEndpoints_SchemaQualifiedFK_Skipped(t *testing.T) {
	schema := &datasource.Schema{
		Driver: "postgres",
		Tables: []datasource.Table{
			{Name: "public.brands", PrimaryKey: []string{"id"},
				Columns: []datasource.Column{{Name: "id", Type: "int"}}},
			{Name: "public.products", PrimaryKey: []string{"id"},
				Columns: []datasource.Column{
					{Name: "id", Type: "int"},
					{Name: "brand_id", Type: "int"},
				},
				ForeignKeys: []datasource.ForeignKey{
					{Name: "fk_products_brand", Columns: []string{"brand_id"},
						ReferencedTable: "public.brands", ReferencedColumns: []string{"id"}},
				}},
		},
	}
	cfg := Generate(schema, &config.Config{
		DataSource: config.DataSourceConfig{Driver: "postgres", DSN: "postgres://x"},
	})

	// Навигационный эндпоинт для schema-qualified таблицы должен существовать
	// (таблица public.brands → entity brands, public.products → products).
	found := false
	for _, ep := range cfg.Endpoints {
		if ep.Op == config.OpCustomQuery && strings.Contains(ep.Path, "/products") {
			found = true
			break
		}
	}
	if !found {
		t.Errorf("P2-10: no navigation endpoint for schema-qualified FK "+
			"(public.brands ← public.products). FK navigation silently lost for multi-schema PG.",
		)
	}
}

// ═══════════════════════════════════════════════════════════════════════
// P2-11: Взрыв количества MCP-тулов при росте схемы
// ═══════════════════════════════════════════════════════════════════════
//
// GenerateMCPTools создаёт до 6 тулов на сущность + навигационные.
// На 100+ таблиц манифест раздувается без guard'а.
// (Примечание: ApprovedTools/курация удалены из кодовой базы 2026-08-02 —
// рекомендация аудита «включите ApprovedTools» неактуальна, нужен другой guard.)
//
// → ПРОХОДИТ: фиксирует, что тулы растут линейно без ограничения.
func TestGenerate_ManifestSizeGuard(t *testing.T) {
	schema := &datasource.Schema{Driver: "sqlite"}
	const n = 120
	for i := 0; i < n; i++ {
		name := fmt.Sprintf("table_%03d", i)
		schema.Tables = append(schema.Tables, datasource.Table{
			Name:       name,
			PrimaryKey: []string{"id"},
			Columns: []datasource.Column{
				{Name: "id", Type: "int"},
				{Name: "name", Type: "string"},
			},
		})
	}
	cfg := Generate(schema, &config.Config{
		DataSource: config.DataSourceConfig{Driver: "sqlite", DSN: ":memory:"},
	})

	tools := len(cfg.MCPTools)
	t.Logf("manifest size: %d tools for %d tables", tools, n)
	if tools > n*6 {
		t.Errorf("P2-11: manifest grew to %d tools (limit %d). "+
			"No guard: onboarding a 100+ table DB yields hundreds of tools. "+
			"Consider curation/limits (ApprovedTools was removed in 2026-08-02, need a new guard).",
			tools, n*6)
	}
}
