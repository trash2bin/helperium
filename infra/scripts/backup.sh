#!/usr/bin/env bash
# Minimal backup script for Helperium platform data.
# Backs up tenant configs and .env (secrets).
# ChromaDB / session data are rebuildable — not backed up.
# Client's production DB is client's responsibility.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
DATA_DIR="${DATA_DIR:-./.data}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

mkdir -p "$BACKUP_DIR/$TIMESTAMP"

# Tenant configs (unique, small)
if [ -d "$DATA_DIR/tenants" ]; then
    cp -r "$DATA_DIR/tenants" "$BACKUP_DIR/$TIMESTAMP/tenants"
    echo "✓ Tenant configs backed up"
fi

# .env (API keys, DSNs)
if [ -f .env ]; then
    cp .env "$BACKUP_DIR/$TIMESTAMP/.env"
    echo "✓ .env backed up"
fi

echo "→ Backup saved to $BACKUP_DIR/$TIMESTAMP"
echo ""
echo "Note: Your production database is YOUR responsibility."
echo "      Configure backups at your hosting provider."
