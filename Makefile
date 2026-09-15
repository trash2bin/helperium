.PHONY: ci ci-lint-py ci-test-py ci-lint-go ci-test-go ci-audit ci-all ci-test-embed build-embed ci-docs ci-e2e ci-test-js ci-test-storefront

ci-lint-py:
	uv run ruff check services/api-service/src/
	uv run ruff format --check services/api-service/src/
	npm install -g pyright 2>/dev/null; pyright

ci-audit:
	-uv audit --preview-features audit-command
	-go install golang.org/x/vuln/cmd/govulncheck@latest
	@echo ""
	@echo "=== Go vulncheck (services/data-service) ==="
	cd services/data-service && $$(go env GOPATH)/bin/govulncheck ./... 2>&1 | grep -E '(No vulnerabilities|Your code is affected|error)' || true
	@echo ""
	@echo "=== Go vulncheck (services/mcp-gateway) ==="
	cd services/mcp-gateway && $$(go env GOPATH)/bin/govulncheck ./... 2>&1 | grep -E '(No vulnerabilities|Your code is affected|error)' || true

ci-test-py:
	CORS_ALLOW_ORIGINS=http://localhost:8080 PYTHONPATH=$(PWD) uv run -- python -m pytest services/api-service/src/api_service/tests/ -v --tb=short
	PYTHONPATH=$(PWD) uv run -- python -m pytest demo/web/tests/ demo/tests/ -v --tb=short
	PYTHONPATH=$(PWD) uv run -- python -m pytest services/rag/tests/unit/ -v --tb=short
	PYTHONPATH=$(PWD) uv run -- python -m pytest services/helperium-sdk/tests/ -v --tb=short
	PYTHONPATH=$(PWD) uv run -- python -m pytest services/agent-db/tests/contract/ -v --tb=short
	PYTHONPATH=$(PWD)/scripts uv run -- python -m pytest scripts/test_cleanup_stale_tenants.py -v --tb=short

GO_SERVICES := ./services/data-service/... ./services/mcp-gateway/... ./services/admin-dashboard/... ./services/helperium-go/...

ci-lint-go:
	go install github.com/golangci/golangci-lint/v2/cmd/golangci-lint@latest
	$$(go env GOPATH)/bin/golangci-lint run $(GO_SERVICES)

ci-fmt-go: ## check go formatting (diff only, fail if not formatted)
	$$(go env GOPATH)/bin/golangci-lint fmt --diff $(GO_SERVICES)

fmt-go: ## fix go formatting
	$$(go env GOPATH)/bin/golangci-lint fmt $(GO_SERVICES)

ci-test-go:
	go test ./services/data-service/... -count=1 -timeout 180s
	go test ./services/mcp-gateway/... -count=1 -timeout 180s
	go test ./services/helperium-go/... -count=1 -timeout 180s

ci-lint-js:
	@echo "=== JS lint (biome) ==="
	npx --yes @biomejs/biome@2.5.4 check --max-diagnostics=500
	@echo "✅ JS lint OK"

ci-test-js:
	@echo "=== Demo web JS tests (node:test) ==="
	node --experimental-vm-modules --test demo/web/static/*.test.mjs
	@echo "✅ JS tests OK"

# The storefront is a foreign project with its own uv environment (not part of
# the helperium workspace), hence --frozen --directory instead of the root env.
# config.test_settings swaps PostgreSQL for in-memory SQLite, so no DB service
# is needed; DJANGO_DEBUG skips the production password checks.
ci-test-storefront:
	@echo "=== Demo storefront tests (Django, SQLite) ==="
	DJANGO_DEBUG=True DJANGO_SETTINGS_MODULE=config.test_settings \
		uv run --frozen --directory demo/autoparts-store python manage.py test tests -v 1
	@echo "✅ Storefront OK"

ci-admin:
	@echo "=== Admin dashboard Go tests ==="
	cd services/admin-dashboard && go test ./...
	@echo "=== Admin dashboard JS tests ==="
	cd services/admin-dashboard && go build -o bin/admin-dashboard ./cmd/server/
	cd services/admin-dashboard/tests && npm test
	@echo "=== Admin dashboard contract check (frontend vs Go routes) ==="
	@echo "  contract check: см. admin-dashboard/tests/contract.test.js (vitest)"
	@echo "✅ Admin dashboard OK"

ci-test-embed:
	@echo "=== Embed widget tests ==="
	cd services/api-service/embed && npm test
	cd services/api-service/embed && bash build.sh
	@echo "✅ Embed widget OK"

ci-docs:
	@echo "=== Documentation path validation ==="
	python3 infra/scripts/check_docs_paths.py
	python3 infra/scripts/test_check_docs_paths.py
	@echo "✅ Documentation paths OK"

# Full E2E pass: rebuilds test-profile Docker images (prevents SDK-in-image
# vs mounted-code drift — the 'DemoSettings has no attribute X' class of
# failures), runs the Docker SDK contract test, then the native E2E suite.
# Not part of default `make ci` (requires Docker + stops dev services via
# `dev.sh e2e-up`). Run before any demo: make ci-e2e
ci-e2e:
	@echo "=== E2E: rebuild test-profile images ==="
	./infra/scripts/compose.sh --profile test build
	@echo "=== E2E: start test profile ==="
	./infra/scripts/dev.sh e2e-up
	@echo "=== E2E: contract tests (image vs code SDK version) ==="
	@echo "    (CONTRACT_STRICT=1: preconditions are FAILURES here, not skips —"
	@echo "     an E2E pass that silently skips the drift control is worthless)"
	CONTRACT_STRICT=1 PYTHONPATH=$$(pwd) uv run -- python -m pytest services/agent-db/tests/contract/ -v --tb=short
	@echo "=== E2E: native suite ==="
	./infra/scripts/dev.sh e2e
	@echo "✅ E2E passed"

build-embed:
	cd services/api-service/embed && bash build.sh
	./infra/scripts/dev.sh restart api
	@echo "✅ Embed widget rebuilt + api-service restarted"

ci: ci-lint-py ci-audit ci-test-py ci-lint-go ci-test-go ci-lint-js ci-test-js ci-test-storefront ci-admin ci-test-embed ci-docs
	@echo "✅ CI passed locally"
