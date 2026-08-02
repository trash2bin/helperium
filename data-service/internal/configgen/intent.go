package configgen

import (
	"log/slog"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// TenantIntent — единственное, что должно быть источником правды на диске.
// Entities/Endpoints/MCPTools/derived CustomQueries/Meta/Version сюда не входят —
// они вычислимы из этого + схемы БД через Hydrate().
type TenantIntent struct {
	DataSource config.DataSourceConfig

	SkipRules            []config.SkipRule
	DisabledDefaultRules []string

	DisplayPrefixes  []string
	CustomPlurals    map[string]string
	CustomShortNames map[string]string

	FilterableRules                []config.FieldRule
	DisabledDefaultFilterableRules []string
	SearchableRules                []config.FieldRule
	DisabledDefaultSearchableRules []string
	EnumRules                      []config.FieldRule
	DisabledDefaultEnumRules       []string

	CustomQueries map[string]config.CustomQuery // ТОЛЬКО explicit (не FK-derived)
	ApprovedTools []config.ApprovedTool

	// Stats — кастомные счётчики для /stats. Generate() создаёт один counter
	// на entity (buildCounters), но через PUT /admin/config можно задать
	// дополнительные counters с ручным Filter — их нужно сохранять.
	Stats *config.StatsConfig

	Introspection *config.IntrospectionConfig
	Auth          *config.AuthConfig
	Server        *config.ServerConfig
}

// DerivedCustomQueryKeys возвращает ключи custom_queries, которые генерирует
// buildNavigationEndpoints из FK — их нельзя путать с explicit-запросами.
//
// ⚠️ Edge case: набор ключей пересчитывается из ТЕКУЩЕЙ схемы каждый раз.
// Если FK удалили/переименовали, buildNavigationEndpoints перестаёт генерировать
// старый ключ, но сам запрос всё ещё лежит в cfg.CustomQueries (записан туда
// предыдущим Hydrate). Тогда ExtractIntent классифицирует его как explicit
// (ключа нет в новом derived-наборе) — и протухший авто-запрос с устаревшими
// именами колонок маскируется под намеренную кастомизацию и переживает все
// будущие Hydrate. Не потеря данных (лишнее сохраняется), но Hydrate залогирует
// warning о коллизии, если FK потом вернут, и разрешит конфликт в пользу
// протухшей версии (result.CustomQueries[k] = v перезаписывает свежую).
func DerivedCustomQueryKeys(entities []config.Entity) map[string]bool {
	_, derived := buildNavigationEndpoints(entities)
	keys := make(map[string]bool, len(derived))
	for k := range derived {
		keys[k] = true
	}
	return keys
}

// ExtractIntent выделяет из полного config.Config только намерения:
// правила, кастомизации, explicit custom queries (без FK-производных).
func ExtractIntent(cfg *config.Config) *TenantIntent {
	derived := DerivedCustomQueryKeys(cfg.Entities)
	explicit := make(map[string]config.CustomQuery)
	for k, v := range cfg.CustomQueries {
		if !derived[k] {
			explicit[k] = v
		}
	}

	return &TenantIntent{
		DataSource:                     cfg.DataSource,
		SkipRules:                      cfg.SkipRules,
		DisabledDefaultRules:           cfg.DisabledDefaultRules,
		DisplayPrefixes:                cfg.DisplayPrefixes,
		CustomPlurals:                  cfg.CustomPlurals,
		CustomShortNames:               cfg.CustomShortNames,
		FilterableRules:                cfg.FilterableRules,
		DisabledDefaultFilterableRules: cfg.DisabledDefaultFilterableRules,
		SearchableRules:                cfg.SearchableRules,
		DisabledDefaultSearchableRules: cfg.DisabledDefaultSearchableRules,
		EnumRules:                      cfg.EnumRules,
		DisabledDefaultEnumRules:       cfg.DisabledDefaultEnumRules,
		CustomQueries:                  explicit,
		ApprovedTools:                  cfg.ApprovedTools,
		Stats:                          cfg.Stats,
		Introspection:                  cfg.Introspection,
		Auth:                           cfg.Auth,
		Server:                         cfg.Server,
	}
}

// Hydrate собирает полный config.Config из intent + схемы БД.
// Генерирует Entities/Endpoints/MCPTools/derived CustomQueries через Generate(),
// затем возвращает explicit custom queries и прочие намерения.
//
// ⚠️ M-3: round-trip ExtractIntent → Hydrate НЕ byte-идемпотентен:
// Generate() регенерирует Meta/Version/Entities/Endpoints/MCPTools из схемы,
// поэтому результат отличается от исходного конфига по этим полям (и не должен
// совпадать — derived-часть всегда отражает текущую схему). Intent-поля
// (DataSource/правила/кастомизации/explicit queries/Stats/ApprovedTools)
// сохраняются идемпотентно. Сравнивать два конфига имеет смысл только по
// intent-полям, не по полному JSON.
func Hydrate(intent *TenantIntent, schema *datasource.Schema) *config.Config {
	genCfg := &config.Config{
		DataSource:                     intent.DataSource,
		SkipRules:                      intent.SkipRules,
		DisabledDefaultRules:           intent.DisabledDefaultRules,
		DisplayPrefixes:                intent.DisplayPrefixes,
		CustomPlurals:                  intent.CustomPlurals,
		CustomShortNames:               intent.CustomShortNames,
		FilterableRules:                intent.FilterableRules,
		DisabledDefaultFilterableRules: intent.DisabledDefaultFilterableRules,
		SearchableRules:                intent.SearchableRules,
		DisabledDefaultSearchableRules: intent.DisabledDefaultSearchableRules,
		EnumRules:                      intent.EnumRules,
		DisabledDefaultEnumRules:       intent.DisabledDefaultEnumRules,
	}

	result := Generate(schema, genCfg)

	// Generate() безусловно перезаписывает result.Stats дефолтными counters
	// (buildCounters). Возвращаем кастомные из intent обратно — как с CustomQueries.
	// Если intent.Stats == nil — оставляем то, что сгенерировал Generate.
	if intent.Stats != nil {
		// Фильтруем counters по реально сгенерированным entity: если кастомный
		// counter ссылается на сущность, которой больше нет в схеме, Config.Validate
		// убьёт весь конфиг при reload/старте. Отбрасываем такие counters с логом.
		entitySet := make(map[string]bool, len(result.Entities))
		for _, e := range result.Entities {
			entitySet[e.Name] = true
		}
		kept := make([]config.Counter, 0, len(intent.Stats.Counters))
		for _, c := range intent.Stats.Counters {
			if !entitySet[c.Entity] {
				slog.Warn("hydrate: dropping stats counter referencing missing entity",
					"counter", c.Name, "entity", c.Entity)
				continue
			}
			kept = append(kept, c)
		}
		result.Stats = intent.Stats
		result.Stats.Counters = kept
	}

	for k, v := range intent.CustomQueries {
		if fresh, exists := result.CustomQueries[k]; exists {
			// Коллизия explicit с FK-derived. Различаем по SQL:
			//  - SQL идентичен авто-паттерну (SELECT t.* FROM {t} t WHERE t.{fk} = ?)
			//    → это протухший derived-запрос (FK удалили/переименовали, ключ выпал
			//    из derived-набора, ExtractIntent принял его за explicit). Отбрасываем,
			//    чтобы не маскировался под намеренную кастомизацию.
			//  - SQL отличается → пользовательская кастомизация под тем же ключом,
			//    сохраняем её (перезаписывает fresh derived), но предупреждаем.
			if v.SQL == fresh.SQL {
				slog.Debug("hydrate: skipping stale FK-derived custom query", "id", k)
				continue
			}
			slog.Warn("custom query id collides with FK-derived query id; user query kept", "id", k)
		}
		if result.CustomQueries == nil {
			result.CustomQueries = make(map[string]config.CustomQuery)
		}
		result.CustomQueries[k] = v
	}

	result.ApprovedTools = intent.ApprovedTools
	result.Introspection = intent.Introspection
	result.Auth = intent.Auth
	result.Server = intent.Server

	return result
}
