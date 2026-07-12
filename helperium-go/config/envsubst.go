package config

import (
	"fmt"
	"strings"
)

// Envsubst подставляет ${ENV} и ${ENV:-default} в строке.
//
// Поддерживаемые синтаксисы:
//
//	${KEY}            — обязательная подстановка. Если getenv возвращает false,
//	                    возвращается ошибка "missing required env var: KEY".
//	${KEY:-default}   — подстановка с дефолтом. Если getenv возвращает false,
//	                    подставляется default (может быть пустой строкой).
//
// Эскейпинг через $$ не поддерживается — в config.json такого синтаксиса нет.
//
// Назначение: работает на этапе raw string load (после os.ReadFile,
// до json.Unmarshal). Это безопасно для config.json, потому что:
//   - в values типа dsn/section names есть ${ENV}
//   - в keys (имена полей) ${ENV} не встречается (snake_case)
//   - в description-строках ${ENV} тоже не используется (естественно для людей)
//
// Если в будущем понадобится различать "это описание" и "это значение",
// нужно будет переходить на json-aware подстановку (после Unmarshal
// пройтись по string-полям). Сейчас — это YAGNI.
func Envsubst(s string, getenv func(string) (string, bool)) (string, error) {
	var b strings.Builder
	b.Grow(len(s))

	i := 0
	for i < len(s) {
		// Ищем начало ${...}.
		start := strings.Index(s[i:], "${")
		if start < 0 {
			b.WriteString(s[i:])
			break
		}
		// Записываем всё до ${ включительно.
		b.WriteString(s[i : i+start])
		i += start + 2 // past "${"

		// Ищем закрывающую }.
		end := strings.Index(s[i:], "}")
		if end < 0 {
			return "", fmt.Errorf("envsubst: unterminated ${ at offset %d: %q", i-2, s)
		}
		expr := s[i : i+end]
		i += end + 1 // past "}"

		// expr может быть "KEY" или "KEY:-default".
		key, def, hasDefault := splitEnvExpr(expr)

		val, ok := getenv(key)
		if !ok {
			if !hasDefault {
				return "", fmt.Errorf("envsubst: missing required env var: %s", key)
			}
			val = def
		}
		b.WriteString(val)
	}

	return b.String(), nil
}

// splitEnvExpr разбирает выражение внутри ${...} на key, default, hasDefault.
//
// Поддерживает ровно один разделитель ":-". Если ":-" встречается внутри
// default — это считается частью default (для совместимости с будущими
// расширениями, но текущий schema этого не требует).
func splitEnvExpr(expr string) (key, def string, hasDefault bool) {
	idx := strings.Index(expr, ":-")
	if idx < 0 {
		return expr, "", false
	}
	return expr[:idx], expr[idx+2:], true
}
