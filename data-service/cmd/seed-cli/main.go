// seed-cli — standalone утилита для заливки seed-данных в пустую БД (dev-only).
//
// Используется для быстрой настройки демо-окружения с тестовыми данными
// университета (группы, студенты, преподаватели, дисциплины, расписание, оценки).
//
// HE prod-инструмент. Не является частью data-service.
// Защита от перезаписи: если БД уже содержит данные — отказ.
//
// Запуск:
//
//	go run ./cmd/seed-cli/                                         # поиск university.db
//	go run ./cmd/seed-cli/ --seed-path path/to/seed.json           # кастомный seed
//	go run ./cmd/seed-cli/ --driver postgres --dsn postgres://...  # PostgreSQL
//
// Переменные окружения:
//
//	DB_PATH       — путь к SQLite (по умолчанию university.db)
//	DB_DRIVER     — sqlite (по умолчанию) или postgres
//	DATABASE_URL  — строка подключения PostgreSQL
package main

import (
	"context"
	"errors"
	"flag"
	"log/slog"
	"os"
	"path/filepath"
	"time"

	"github.com/trash2bin/helperium/data-service/internal/datasource"
	"github.com/trash2bin/helperium/data-service/internal/seedgen"
)

const defaultSeedPath = "fixtures/seed.json"

func main() {
	seedPath := flag.String("seed-path", defaultSeedPath, "путь к JSON с seed-данными")
	driver := flag.String("driver", "", "драйвер БД: sqlite (по умолчанию) или postgres")
	dsn := flag.String("dsn", "", "DSN для подключения (альтернатива env)")
	flag.Parse()

	// Logger (plain text для CLI)
	slog.SetDefault(slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelInfo})))

	// Драйвер
	drv := *driver
	if drv == "" {
		drv = os.Getenv("DB_DRIVER")
	}
	if drv == "" {
		drv = "sqlite"
	}

	// DSN
	dsnStr := *dsn
	if dsnStr == "" {
		switch drv {
		case "sqlite":
			p := os.Getenv("DB_PATH")
			if p == "" {
				p = "university.db"
			}
			abs, err := filepath.Abs(p)
			if err != nil {
				slog.Error("resolve DB_PATH", "error", err)
				os.Exit(1)
			}
			dsnStr = abs
		case "postgres":
			url := os.Getenv("DATABASE_URL")
			if url == "" {
				slog.Error("DATABASE_URL required for postgres driver")
				os.Exit(1)
			}
			dsnStr = url
		default:
			slog.Error("unsupported driver", "driver", drv)
			os.Exit(1)
		}
	}

	slog.Info("seed-cli starting", "driver", drv, "dsn", dsnStr)

	// Открываем БД
	registry := datasource.NewDefaultRegistry()
	adapter, ok := registry.Get(drv)
	if !ok {
		slog.Error("unsupported driver", "driver", drv)
		os.Exit(1)
	}

	conn, err := adapter.Connect(context.Background(), dsnStr)
	if err != nil {
		slog.Error("connect to database", "error", err)
		os.Exit(1)
	}
	defer func() { _ = conn.Close() }()

	// Загружаем seed
	absPath, err := filepath.Abs(*seedPath)
	if err != nil {
		slog.Error("resolve seed path", "error", err)
		os.Exit(1)
	}
	slog.Info("loading seed", "path", absPath)

	seed, err := seedgen.Load(absPath)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			slog.Error("seed file not found", "path", absPath)
		} else {
			slog.Error("load seed", "error", err)
		}
		os.Exit(1)
	}

	// Применяем
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	if err := seedgen.Apply(ctx, conn, seed); err != nil {
		slog.Error("apply seed", "error", err)
		os.Exit(1)
	}

	slog.Info("seed completed successfully")
}
