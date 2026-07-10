package seedgen

import (
	"fmt"
	"strings"

	"github.com/agent-tutor/agent-tutor-go/config"
)

// GenerateDDL generates CREATE TABLE DDL from config entities.
//
// driver — "sqlite" or "postgres". Affects:
//   - autoincrement syntax (INTEGER PRIMARY KEY AUTOINCREMENT vs SERIAL)
//   - TEXT vs VARCHAR
//   - identifier quoting
//   - FK strategy: inline REFERENCES for SQLite, ALTER TABLE for Postgres
//
// Returns multi-statement DDL separated by ";\n".
func GenerateDDL(entities []config.Entity, driver string) (string, error) {
	if len(entities) == 0 {
		return "", fmt.Errorf("seedgen: GenerateDDL: no entities")
	}

	var stmts []string
	var fkStmts []string

	for _, e := range entities {
		if e.Table == "" || e.IDColumn == "" || len(e.Fields) == 0 {
			continue
		}

		// Pre-collect FK references for SQLite inline embedding.
		// key: LocalFK column, value: "REFERENCES target_table(target_column)"
		fkRefByColumn := make(map[string]string)
		for _, r := range e.Relations {
			if r.Kind != config.RelationManyToOne {
				break
			}
			ref := fmt.Sprintf("REFERENCES %s(%s)", quoteIdent(r.Table, driver), quoteIdent(r.TargetFK, driver))
			if driver == "sqlite" {
				// SQLite: embed REFERENCES in CREATE TABLE column definition
				fkRefByColumn[r.LocalFK] = ref
			} else {
				// Postgres: add FK via ALTER TABLE after CREATE
				fkName := fmt.Sprintf("fk_%s_%s", e.Table, r.Field)
				alter := fmt.Sprintf(
					"ALTER TABLE %s ADD CONSTRAINT %s FOREIGN KEY (%s) REFERENCES %s (%s)",
					quoteIdent(e.Table, driver),
					quoteIdent(fkName, driver),
					quoteIdent(r.LocalFK, driver),
					quoteIdent(r.Table, driver),
					quoteIdent(r.TargetFK, driver),
				)
				fkStmts = append(fkStmts, alter)
			}
		}

		var cols []string
		var pkCols []string
		for _, f := range e.Fields {
			colDef := fmt.Sprintf("%s %s", quoteIdent(f.Column, driver), fieldToSQLType(f, driver))
			if f.Nullable != nil && !*f.Nullable {
				colDef += " NOT NULL"
			}
			// Embed FK reference for SQLite (inline REFERENCES)
			if ref, ok := fkRefByColumn[f.Column]; ok {
				colDef += " " + ref
			}
			if f.PrimaryKey != nil && *f.PrimaryKey {
				// Collect PK-flagged columns and emit a single composite
				// PRIMARY KEY (...) clause after the column loop. Inline
				// ` PRIMARY KEY` per column is rejected by PostgreSQL when
				// a table has more than one PK column (SQLSTATE 42P16).
				pkCols = append(pkCols, quoteIdent(f.Column, driver))
			}
			cols = append(cols, colDef)
		}

		if len(pkCols) > 0 {
			// Single flagged column -> PRIMARY KEY ("id") (Postgres accepts this).
			// Multiple flagged columns -> PRIMARY KEY ("a", "b") (composite).
			cols = append(cols, fmt.Sprintf("PRIMARY KEY (%s)", strings.Join(pkCols, ", ")))
		} else if e.IDColumn != "" {
			// Fallback for tables with no flagged PK field (e.g. sqitch_* migration tables).
			cols = append(cols, fmt.Sprintf("PRIMARY KEY (%s)", quoteIdent(e.IDColumn, driver)))
		}

		create := fmt.Sprintf("CREATE TABLE IF NOT EXISTS %s (\n  %s\n)", quoteIdent(e.Table, driver), strings.Join(cols, ",\n  "))
		stmts = append(stmts, create)
	}

	stmts = append(stmts, fkStmts...)

	return strings.Join(stmts, ";\n") + ";", nil
}

// fieldToSQLType maps config.FieldType to SQL column type.
// Currently returns the same type for both SQLite and PostgreSQL;
// driver is kept for future per-driver differentiation (e.g. SMALLINT vs INTEGER).
func fieldToSQLType(f config.EntityField, driver string) string {
	switch f.Type {
	case config.FieldTypeString:
		return "TEXT"
	case config.FieldTypeInt:
		return "INTEGER"
	case config.FieldTypeFloat:
		return "REAL"
	case config.FieldTypeBool:
		if driver == "postgres" {
			return "BOOLEAN"
		}
		return "INTEGER" // SQLite has no BOOLEAN
	case config.FieldTypeJSON:
		if driver == "postgres" {
			return "JSONB"
		}
		return "TEXT" // SQLite stores JSON as text
	case config.FieldTypeDatetime, config.FieldTypeDate:
		return "TEXT" // ISO 8601
	default:
		return "TEXT"
	}
}

// quoteIdent quotes an identifier (table/column name) in driver-specific syntax.
// Currently both SQLite and PostgreSQL use double quotes; driver parameter
// is kept for future portability (e.g. MySQL backticks).
func quoteIdent(name, driver string) string {
	return `"` + name + `"`
}
