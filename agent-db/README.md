# agent-db

Unified CLI for helperium database, scenario, tenant, seed, and test management.

## Installation

```bash
cd helperium/agent-db
uv pip install -e .
```

## Commands

```bash
# Scenario management
agent-db scenario list
agent-db scenario materialize university --force
agent-db scenario validate university

# Tenant management
agent-db tenant register university --config scenarios/university/config.json
agent-db tenant list
agent-db tenant delete university

# Seed management
agent-db seed generate university --students 100
agent-db seed load university

# Test orchestration
agent-db test e2e --tenants university,shop
agent-db test isolation --tenants university,shop
agent-db test dynamic-tools --tenant university
agent-db test composite-tools                   # 🆕 composite multi-tenant MCP тест
agent-db test all

# E2E CLI commands (click commands)
uv run agent-db e2e-mcp                         # per-tenant MCP тесты (3 теста, backward compat)
uv run agent-db e2e-mcp-composite               # 🆕 composite multi-tenant MCP тесты (3 теста)
```

## Architecture

```
agent-db/
├── cli.py              # Click entry point
├── core/               # Shared utilities
│   ├── config.py       # Config loading/validation
│   ├── http.py         # HTTP client for data-service
│   └── paths.py        # Path resolution
├── scenario/           # Scenario commands
├── tenant/             # Tenant commands
├── seed/               # Seed commands
└── test/               # Test commands
```
