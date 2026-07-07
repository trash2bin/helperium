// Общие хелперы для тестов адаптеров (package datasource_test).
//
// Используются в sqlite_adapter_test.go и postgres_adapter_test.go.
package datasource_test

// equalStringSlices — поэлементное сравнение, nil-эквивалентно [].
func equalStringSlices(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
