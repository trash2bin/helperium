// Package seedgen — materialize: создание полностью готовой БД из сценария (config.json + seed.json).
//
// Используется через CLI-флаг --materialize в data-service.
// Сценарий — самодостаточная директория с config.json (описывает схему + эндпоинты)
// и seed.json (данные). Materialize генерирует DDL из entities конфига и создаёт БД.
//
// Пример сценария: testdata/scenarios/sqlite-testseed/
//
//	config.json — entities + endpoints + custom_queries
//	seed.json   — seedgen.Seed
//
// После materialize можно запустить data-service с этим же config.json:
//
//	data-service --config testdata/scenarios/sqlite-testseed/config.json
package seedgen

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"strings"

	"github.com/trash2bin/helperium/helperium-go/config"
	"github.com/trash2bin/helperium/data-service/internal/datasource"
)

// Materialize создаёт БД из сценария:
//  1. Генерирует DDL из cfg.Entities через GenerateDDL
//  2. Если force=true:
//     - SQLite: удаляет файл БД (если DSN — путь к файлу)
//     - PostgreSQL: DROP SCHEMA public CASCADE; CREATE SCHEMA public
//  3. Подключается к БД через adapter
//  4. Применяет DDL
//  5. Заливает seed-данные (идемпотентно — ON CONFLICT DO NOTHING для PG, OR IGNORE для SQLite)
//
// baseDir — директория сценария, относительно которой резолвятся относительные DSN.
// Возвращает ошибку если любой шаг не удался.
func Materialize(ctx context.Context, adapter datasource.Adapter, cfg *config.Config, seed *Seed, baseDir string, force bool) error {
	driver := string(cfg.DataSource.Driver)

	// 1. Генерируем DDL
	ddl, err := GenerateDDL(cfg.Entities, driver)
	if err != nil {
		return fmt.Errorf("materialize: generate DDL: %w", err)
	}

	dsn := cfg.DataSource.DSN

	// Резолвим относительные пути DSN относительно директории сценария
	if !isMemoryDSN(dsn) && !isAbsolutePath(dsn) && driver == "sqlite" {
		dsn = filepath.Join(baseDir, dsn)
	}

	// 2. Если force и SQLite — удаляем существующий файл БД
	if force && driver == "sqlite" && !isMemoryDSN(dsn) {
		if _, statErr := os.Stat(dsn); statErr == nil {
			slog.Info("materialize: force — removing existing database", "path", dsn)
			if err := os.Remove(dsn); err != nil {
				return fmt.Errorf("materialize: force remove %q: %w", dsn, err)
			}
			// Также удаляем WAL и SHM если есть
			for _, suffix := range []string{"-wal", "-shm"} {
				_ = os.Remove(dsn + suffix)
			}
		}
	}

	// 2b. Если force и PostgreSQL — DROP SCHEMA public CASCADE; CREATE SCHEMA public
	if force && driver == "postgres" {
		slog.Info("materialize: force — dropping and recreating schema public")
		forceConn, err := adapter.Connect(ctx, dsn)
		if err != nil {
			return fmt.Errorf("materialize: force connect to %q: %w", dsn, err)
		}
		_, err = forceConn.ExecContext(ctx, "DROP SCHEMA public CASCADE")
		_ = forceConn.Close()
		if err != nil {
			return fmt.Errorf("materialize: force DROP SCHEMA public: %w", err)
		}
		// Reconnect for CREATE SCHEMA (must be in its own batch after drop)
		forceConn2, err := adapter.Connect(ctx, dsn)
		if err != nil {
			return fmt.Errorf("materialize: force reconnect to %q: %w", dsn, err)
		}
		_, err = forceConn2.ExecContext(ctx, "CREATE SCHEMA public")
		_ = forceConn2.Close()
		if err != nil {
			return fmt.Errorf("materialize: force CREATE SCHEMA public: %w", err)
		}
	}

	// 3. Подключаемся
	conn, err := adapter.Connect(ctx, dsn)
	if err != nil {
		return fmt.Errorf("materialize: connect to %q: %w", dsn, err)
	}
	defer conn.Close() //nolint:errcheck

	// 4. Применяем DDL + seed
	phFn := SQLitePlaceholder
	if driver == "postgres" {
		phFn = PostgresPlaceholder
	}
	// seed может быть nil — схема всё равно создастся, данные не вставятся.
	if seed == nil {
		seed = &Seed{}
	}
	if err := ApplyWithDDL(ctx, conn, ddl, seed, phFn, driver); err != nil {
		return fmt.Errorf("materialize: apply: %w", err)
	}

	slog.Info("materialize: database created",
		"driver", driver,
		"dsn", dsn,
		"entities", len(cfg.Entities),
		"groups", len(seed.Groups),
		"students", len(seed.Students),
	)

	return nil
}

// isMemoryDSN возвращает true если DSN указывает на in-memory SQLite БД.
func isMemoryDSN(dsn string) bool {
	return dsn == ":memory:" || strings.HasPrefix(dsn, ":memory:?")
}

// isAbsolutePath возвращает true если путь абсолютный или начинается с postgres://.
func isAbsolutePath(dsn string) bool {
	if strings.HasPrefix(dsn, "postgres://") || strings.HasPrefix(dsn, "postgresql://") {
		return true
	}
	return strings.HasPrefix(dsn, "/")
}
