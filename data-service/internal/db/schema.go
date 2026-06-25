// Package db — embedded DDL для SQLite/PostgreSQL.
// Используется в seed-режиме data-service (--seed) и в Go-тестах.
package db

import _ "embed"

//go:embed schema.sql
var SchemaSQL string