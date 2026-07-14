.PHONY: ci ci-lint-py ci-test-py ci-lint-go ci-test-go ci-audit ci-all

ci-lint-py:
	uv run ruff check api-service/src/
	uv run ruff format --check api-service/src/
	npm install -g pyright 2>/dev/null; pyright

ci-audit:
	-uv audit --preview-features audit-command
	@echo ""
	@echo "=== Go vulncheck (data-service) ==="
	cd data-service && $$(go env GOPATH)/bin/govulncheck ./... 2>&1 | grep -E '(No vulnerabilities|Your code is affected|error)' || true
	@echo ""
	@echo "=== Go vulncheck (mcp-gateway) ==="
	cd mcp-gateway && $$(go env GOPATH)/bin/govulncheck ./... 2>&1 | grep -E '(No vulnerabilities|Your code is affected|error)' || true

ci-test-py:
	PYTHONPATH=$(PWD) uv run -- python -m pytest api-service/src/api_service/tests/ -v --tb=short
	PYTHONPATH=$(PWD) uv run -- python -m pytest demo/web/tests/ demo/tests/ -v --tb=short
	PYTHONPATH=$(PWD) uv run -- python -m pytest rag/tests/unit/ -v --tb=short
	PYTHONPATH=$(PWD) uv run -- python -m pytest helperium-sdk/tests/ -v --tb=short

ci-lint-go:
	go install github.com/golangci/golangci-lint/v2/cmd/golangci-lint@latest
	golangci-lint run ./data-service/...
	golangci-lint run ./mcp-gateway/...

ci-test-go:
	go test ./data-service/... -count=1 -timeout 180s
	go test ./mcp-gateway/... -count=1 -timeout 180s

ci-admin:
	@echo "=== Admin dashboard JS tests ==="
	cd admin-dashboard && go build -o bin/admin-dashboard ./cmd/server/
	cd admin-dashboard/tests && npm test
	@echo "✅ Admin dashboard OK"

ci: ci-lint-py ci-audit ci-test-py ci-lint-go ci-test-go ci-admin
	@echo "✅ CI passed locally"
