// Package datasource — конструкторы готовых реестров адаптеров.

package datasource

// NewDefaultRegistry возвращает Registry со всеми адаптерами, скомпилированными
// в бинарник. На данный момент: SQLite + PostgreSQL.
//
// Используется в internal/db/connector.go как единственная точка регистрации.
// Если в фазе 3.x появятся другие СУБД (MySQL, MSSQL) — добавлять сюда.
//
// Паника при попытке зарегистрировать один и тот же driver дважды —
// programming error, должна ловиться на старте приложения.
func NewDefaultRegistry() *Registry {
	r := NewRegistry()
	r.Register(SqliteAdapter{})
	r.Register(PostgresAdapter{})
	return r
}
