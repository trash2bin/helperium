.PHONY: ci ci-lint-py ci-test-py ci-lint-go ci-test-go ci-all

ci-lint-py:
	uv run ruff check api-service/src/
	uv run ruff format --check api-service/src/
	npm install -g pyright 2>/dev/null; pyright

ci-test-py:
	PYTHONPATH=$(PWD) uv run pytest api-service/src/api_service/tests/ -v --tb=short

ci-lint-go:
	go install github.com/golangci/golangci-lint/v2/cmd/golangci-lint@latest
	golangci-lint run ./data-service/...
	golangci-lint run ./mcp-gateway/...

ci-test-go:
	go test ./data-service/... -count=1 -timeout 180s
	go test ./mcp-gateway/... -count=1 -timeout 180s

ci: ci-lint-py ci-test-py ci-lint-go ci-test-go
	@echo "✅ CI passed locally"
