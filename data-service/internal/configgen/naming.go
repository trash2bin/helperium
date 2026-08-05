package configgen

import (
	"fmt"
	"strings"

	"github.com/trash2bin/helperium/helperium-go/config"
)

// DefaultDisplayPrefixes returns common table name prefixes to strip when generating
// human-readable display names for entities and tools.
func DefaultDisplayPrefixes() []string {
	return []string{"catalog_", "auth_", "django_"}
}

// shortBusinessName отрезает префикс (catalog_, auth_, django_) и
// возвращает читаемое имя.
// customShortNames имеет высший приоритет (из конфига).
func shortBusinessName(name string, displayPrefixes []string, customShortNames map[string]string) string {
	// CustomShortNames — первый приоритет (полное имя)
	if cn, ok := customShortNames[name]; ok {
		return cn
	}

	for _, pfx := range displayPrefixes {
		if strings.HasPrefix(name, pfx) {
			result := strings.TrimPrefix(name, pfx)
			// CustomShortNames — первый приоритет (короткое имя)
			if cn, ok := customShortNames[result]; ok {
				return cn
			}
			// Fallback: хардкор
			if result == "cartitem" {
				return "Cart item"
			}
			if result == "sitesettings" {
				return "Settings"
			}
			return titleCase(result)
		}
	}

	// Попробовать полным именем (без префикса) — на случай если cfg.CustomShortNames не пуст
	return titleCase(name)
}

// titleCase capitalises the first letter of a string (unicode-safe).
func titleCase(s string) string {
	if s == "" {
		return ""
	}
	r := []rune(s)
	return strings.ToUpper(string(r[0])) + string(r[1:])
}

// shortColumnName делает snake_case колонку читаемой для LLM.
func shortColumnName(name string) string {
	// Простейшее преобразование: _ → пробел
	result := strings.ReplaceAll(name, "_", " ")
	// Если выглядит как FK (_id), подчёркиваем
	if strings.HasSuffix(name, "_id") {
		result = strings.TrimSuffix(result, " id") + " ID"
	}
	return result
}

// CanonicalEntityName резолвит display-имя сущности (которое модель видит в
// db_map / mcp-манифесте) обратно в canonical имя (catalog_*), которое принимает
// /q/* резолвер. Это закрывает контрактную дыру: db_map показывал "Brand (catalog_brand)",
// модель копировала "Brand" в entity-параметр → 404 unknown_entity.
//
// Принимает (case-insensitive):
//   - canonical имя: "catalog_brand" → "catalog_brand"
//   - display-имя: "Brand" → "catalog_brand"
//   - полное display-имя из db_map: "Brand (catalog_brand)" → "catalog_brand"
//   - titleCase без префикса: "Products" → "catalog_product" (мн. число)
//
// Возвращает "" для неизвестного имени (вызывающий отвечает 404).
func CanonicalEntityName(name string, displayPrefixes []string, customShortNames map[string]string, entityMap map[string]config.Entity) string {
	if name == "" {
		return ""
	}

	// 1. Прямое попадание canonical.
	if _, ok := entityMap[name]; ok {
		return name
	}

	// 2. Полное display-имя из db_map: "Brand (catalog_brand)".
	// Вытаскиваем часть в скобках — это canonical.
	if open := strings.LastIndex(name, "("); open >= 0 {
		if close := strings.Index(name[open:], ")"); close >= 0 {
			inner := strings.TrimSpace(name[open+1 : open+close])
			if _, ok := entityMap[inner]; ok {
				return inner
			}
		}
	}

	// 3. Display-имя (titleCase) или его lowercase → ищем по reverse-индексу.
	// Строим reverse-индекс display → canonical из той же логики shortBusinessName,
	// что генерирует db_map — гарантия, что то, что система показывает, она и принимает.
	canonicalByName := make(map[string]string, len(entityMap))
	for cn := range entityMap {
		display := shortBusinessName(cn, displayPrefixes, customShortNames)
		canonicalByName[strings.ToLower(display)] = cn
		// Мн. число display-имени (модель может прислать "Products").
		canonicalByName[strings.ToLower(pluralizeEntity(cn, displayPrefixes, nil))] = cn
	}
	if cn, ok := canonicalByName[strings.ToLower(strings.TrimSpace(name))]; ok {
		return cn
	}

	// 4. Display-имя без префикса (catalog_ отрезан) → canonical с префиксом.
	// Например "brand" → "catalog_brand".
	for cn := range entityMap {
		for _, pfx := range displayPrefixes {
			if strings.HasPrefix(cn, pfx) {
				short := strings.TrimPrefix(cn, pfx)
				if strings.EqualFold(strings.TrimSpace(name), short) {
					return cn
				}
			}
		}
	}

	return ""
}

// pluralizeEntity returns the English plural form of an entity name.
func pluralizeEntity(name string, displayPrefixes []string, customPlurals map[string]string) string {
	// сначала проверяем customPlurals из конфига
	if p, ok := customPlurals[name]; ok {
		return p
	}
	special := map[string]string{
		"product":      "products",
		"brand":        "brands",
		"category":     "categories",
		"order":        "orders",
		"cart":         "cart",
		"cartitem":     "cart_items",
		"sitesettings": "settings",
		"user":         "users",
		"group":        "groups",
	}
	if p, ok := special[name]; ok {
		return p
	}
	// Check by short name (after stripping prefix)
	short := name
	for _, prefix := range displayPrefixes {
		if strings.HasPrefix(short, prefix) {
			short = strings.TrimPrefix(short, prefix)
			break
		}
	}
	if p, ok := special[short]; ok {
		return p
	}
	if strings.HasSuffix(short, "s") {
		return short
	}
	if strings.HasSuffix(short, "y") {
		return short[:len(short)-1] + "ies"
	}
	return short + "s"
}

// toolDisplayName generates a human-readable English display name for a tool.
func toolDisplayName(op, entityName string, displayPrefixes []string, customPlurals map[string]string) string {
	short := entityName
	for _, prefix := range displayPrefixes {
		if strings.HasPrefix(short, prefix) {
			short = strings.TrimPrefix(short, prefix)
			break
		}
	}
	plural := pluralizeEntity(entityName, displayPrefixes, customPlurals)
	switch op {
	case string(config.OpGetByID):
		return fmt.Sprintf("%s by ID", short)
	case string(config.OpCount):
		return fmt.Sprintf("Count %s", plural)
	case string(config.OpDistinct):
		return fmt.Sprintf("Distinct %s", plural)
	case "grep":
		return fmt.Sprintf("Search %s", plural)
	case "filter":
		return fmt.Sprintf("Filter %s", plural)
	case "schema":
		return fmt.Sprintf("Schema of %s", short)
	default:
		return ""
	}
}
