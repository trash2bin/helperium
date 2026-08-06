package configgen

import (
	"strings"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/helperium-go/config"
)

// tableToEntity конвертирует datasource.Table → config.Entity.
//
// Name в config.Entity должен проходить regex ^[a-z][a-z0-9_]*$ (JSON Schema),
// поэтому для многосхемных БД (Postgres: "public.customers") используем
// только последний сегмент (без префикса схемы). Table в config.Entity
// хранит полное имя — QueryBuilder использует его для SQL.
// Если у таблицы нет PRIMARY KEY (миграционные таблицы реальной prod-БД),
// id_column берётся как первая колонка — иначе JSON-Schema реджектит пустую.
func tableToEntity(tbl datasource.Table, displayPrefixes []string) config.Entity {
	shortName := tbl.Name
	if idx := strings.LastIndex(shortName, "."); idx >= 0 {
		shortName = shortName[idx+1:]
	}

	fields := make([]config.EntityField, 0, len(tbl.Columns))
	pkSet := make(map[string]bool, len(tbl.PrimaryKey))
	for _, pk := range tbl.PrimaryKey {
		pkSet[pk] = true
	}

	colNames := make([]string, 0, len(tbl.Columns))
	for _, col := range tbl.Columns {
		nullable := col.Nullable
		isPK := pkSet[col.Name]
		fields = append(fields, config.EntityField{
			Name:        col.Name,
			Column:      col.Name,
			Type:        config.FieldType(col.Type),
			Nullable:    &nullable,
			PrimaryKey:  &isPK,
			Description: col.Description,
		})
		colNames = append(colNames, col.Name)
	}

	idCol := ""
	if len(tbl.PrimaryKey) > 0 {
		idCol = tbl.PrimaryKey[0]
	}
	if idCol == "" && len(colNames) > 0 {
		idCol = colNames[0]
	}

	// Auto-generate Relations из ForeignKeys.
	// Каждый FK-constraint с одной колонкой → Relation (many_to_one).
	relations := make([]config.Relation, 0, len(tbl.ForeignKeys))
	for _, fk := range tbl.ForeignKeys {
		if len(fk.Columns) != 1 || len(fk.ReferencedColumns) != 1 {
			continue // composite FK пока пропускаем
		}
		targetTable := fk.ReferencedTable
		if idx := strings.LastIndex(targetTable, "."); idx >= 0 {
			targetTable = targetTable[idx+1:]
		}
		relations = append(relations, config.Relation{
			Field:   fk.Columns[0],
			Kind:    config.RelationManyToOne,
			Table:   targetTable,
			LocalFK: fk.Columns[0],
		})
	}

	return config.Entity{
		Name:      shortName,
		Table:     tbl.Name,
		IDColumn:  idCol,
		Fields:    fields,
		Relations: relations,
	}
}
