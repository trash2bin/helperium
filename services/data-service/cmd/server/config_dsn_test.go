package main

// TestConfigDSNResolvesToExistingFile — детерминированная локальная
// воспроизводимость CI-бага «data-service не стартует: sqlite: unable to
// open database file».
//
// После реструктуризации (services/ + infra/) в конфигах остались пути к
// data-service без префикса services/ (например "../data-service/testdata/..."),
// что резолвится в несуществующую директорию — data-service падает на
// bootstrap tenant (health 503 → compose up падает).
//
// Тест повторяет логику резолва DSN из main.go:
//
//	if sqlite && !abs && dsn != ":memory:" {
//	    dsn = filepath.Join(filepath.Dir(absCfgPath), dsn)
//	}
//
// и проверяет, что файл существует. Падает с понятным сообщением о том,
// какой конфиг и какой DSN битый — без Docker, без Colima, одним go test.

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/trash2bin/helperium/helperium-go/config"
)

// configsToCheck — конфиги, которыми реально стартует data-service
// (спецификация + все sqlite-сценарии). PostgreSQL-конфиги пропущены
// (не file-based).
func configsToCheck() []string {
	repoRoot := findRepoRoot()

	return []string{
		filepath.Join(repoRoot, "specs", "config.example.json"),
		filepath.Join(repoRoot, "services", "data-service", "testdata", "scenarios", "shop", "config.json"),
		filepath.Join(repoRoot, "services", "data-service", "testdata", "scenarios", "sqlite-testseed", "config.json"),
		filepath.Join(repoRoot, "services", "data-service", "testdata", "scenarios", "big-testseed", "config.json"),
	}
}

// findRepoRoot поднимается от cwd (каталог теста) до корня репо —
// первого каталога, где есть go.work или .git (надёжнее, чем считать "..").
func findRepoRoot() string {
	dir, err := os.Getwd()
	if err != nil {
		panic(err)
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, "go.work")); err == nil {
			return dir
		}
		if _, err := os.Stat(filepath.Join(dir, ".git")); err == nil {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			panic("repo root not found (no go.work / .git up the tree)")
		}
		dir = parent
	}
}

func TestConfigDSNResolvesToExistingFile(t *testing.T) {
	for _, cfgPath := range configsToCheck() {
		cfgPath := cfgPath
		t.Run(filepath.Base(filepath.Dir(cfgPath)), func(t *testing.T) {
			if _, err := os.Stat(cfgPath); os.IsNotExist(err) {
				t.Skipf("config not present: %s", cfgPath)
			}

			cfg, err := config.Load(cfgPath)
			if err != nil {
				t.Fatalf("load config %s: %v", cfgPath, err)
			}

			if cfg.DataSource.Driver != config.DriverSQLite {
				t.Skipf("driver %q — not file-based", cfg.DataSource.Driver)
			}

			dsn := cfg.DataSource.DSN
			if strings.Contains(dsn, "://") {
				t.Skipf("dsn %q — not a local file", dsn)
			}

			absCfgPath, err := filepath.Abs(cfgPath)
			if err != nil {
				t.Fatalf("abs config path: %v", err)
			}

			// ── Тот же резолв, что в main.go ──
			if !filepath.IsAbs(dsn) && dsn != ":memory:" && !strings.HasPrefix(dsn, ":memory:?") {
				dsn = filepath.Join(filepath.Dir(absCfgPath), dsn)
			}

			if _, err := os.Stat(dsn); os.IsNotExist(err) {
				t.Fatalf(
					"config %s: DSN %q resolves to %q which does not exist — "+
						"путь сломан после реструктуризации (нужен префикс services/?)",
					cfgPath, cfg.DataSource.DSN, dsn,
				)
			}
		})
	}
}
