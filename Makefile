.PHONY: ci ci-lint-py ci-test-py ci-lint-go ci-test-go ci-audit ci-all ci-test-embed build-embed ci-docs

ci-lint-py:
	uv run ruff check services/api-service/src/
	uv run ruff format --check services/api-service/src/
	npm install -g pyright 2>/dev/null; pyright

ci-audit:
	-uv audit --preview-features audit-command
	@echo ""
	@echo "=== Go vulncheck (services/data-service) ==="
	cd services/data-service && $$(go env GOPATH)/bin/govulncheck ./... 2>&1 | grep -E '(No vulnerabilities|Your code is affected|error)' || true
	@echo ""
	@echo "=== Go vulncheck (services/mcp-gateway) ==="
	cd services/mcp-gateway && $$(go env GOPATH)/bin/govulncheck ./... 2>&1 | grep -E '(No vulnerabilities|Your code is affected|error)' || true

ci-test-py:
	PYTHONPATH=$(PWD) uv run -- python -m pytest services/api-service/src/api_service/tests/ -v --tb=short
	PYTHONPATH=$(PWD) uv run -- python -m pytest demo/web/tests/ demo/tests/ -v --tb=short
	PYTHONPATH=$(PWD) uv run -- python -m pytest services/rag/tests/unit/ -v --tb=short
	PYTHONPATH=$(PWD) uv run -- python -m pytest services/helperium-sdk/tests/ -v --tb=short

ci-lint-go:
	go install github.com/golangci/golangci-lint/v2/cmd/golangci-lint@latest
	$$(go env GOPATH)/bin/golangci-lint run ./services/data-service/...
	$$(go env GOPATH)/bin/golangci-lint run ./services/mcp-gateway/...

ci-test-go:
	go test ./services/data-service/... -count=1 -timeout 180s
	go test ./services/mcp-gateway/... -count=1 -timeout 180s

ci-lint-js:
	@echo "=== JS lint (biome) ==="
	npx @biomejs/biome check --max-diagnostics=500
	@echo "✅ JS lint OK"

ci-admin:
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
	@echo "=== Docs: мёртвые пути ==="
	python3 infra/scripts/check_docs_paths.py
	@echo "✅ Docs paths OK"

build-embed:
	cd services/api-service/embed && bash build.sh
	./infra/scripts/dev.sh restart api
	@echo "✅ Embed widget rebuilt + api-service restarted"

ci: ci-lint-py ci-audit ci-test-py ci-lint-go ci-test-go ci-lint-js ci-admin ci-test-embed ci-docs
	@echo "✅ CI passed locally"
