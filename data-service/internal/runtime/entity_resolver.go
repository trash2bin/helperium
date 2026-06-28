package runtime

import "fmt"

// EntityResolver — маппинг между публичными именами сущностей/полей
// и реальными именами таблиц/колонок в БД.
//
// Резолвер хранит индекси по двум направлениям:
//   - публичное имя entity → Entity
//   - внутри Entity — публичное имя поля → колонка и обратно.
//
// Все методы безопасны для чтения без блокировок после конструктора.
// После NewEntityResolver состояние не меняется.
type EntityResolver struct {
	entities map[string]Entity
}

// NewEntityResolver строит resolver по списку сущностей.
//
// Если две entity имеют одинаковое публичное имя (Entity.Name) —
// это программная ошибка конфигурации, и resolver возвращает ошибку
// без построения частичного состояния.
func NewEntityResolver(entities []Entity) (*EntityResolver, error) {
	idx := make(map[string]Entity, len(entities))
	for _, e := range entities {
		if _, dup := idx[e.Name]; dup {
			return nil, fmt.Errorf(
				"runtime: duplicate entity name %q", e.Name,
			)
		}
		idx[e.Name] = e
	}
	return &EntityResolver{entities: idx}, nil
}

// Resolve возвращает Entity по её публичному имени.
// Возвращает (Entity{}, false), если сущность не найдена.
func (r *EntityResolver) Resolve(name string) (Entity, bool) {
	e, ok := r.entities[name]
	return e, ok
}

// ColumnFor возвращает имя колонки в БД для публичного имени поля.
//
// Поиск линейный по entity.Fields — для типичной entity (< 50 полей)
// это дешевле хеш-индексации. Если в будущем появятся entity с
// сотнями полей — стоит добавить индекс в EntityResolver.
func (r *EntityResolver) ColumnFor(entity Entity, publicField string) (string, bool) {
	for _, f := range entity.Fields {
		if f.Name == publicField {
			return f.Column, true
		}
	}
	return "", false
}

// PublicFor возвращает публичное имя поля по имени колонки в БД.
//
// Обратное направление для ColumnFor. Используется в response_mapper,
// когда *sql.Rows отдаёт имена колонок и нужно вернуть публичные имена
// в JSON-ответе.
func (r *EntityResolver) PublicFor(entity Entity, column string) (string, bool) {
	for _, f := range entity.Fields {
		if f.Column == column {
			return f.Name, true
		}
	}
	return "", false
}

// AllEntities возвращает список публичных имён всех зарегистрированных
// сущностей в неопределённом порядке (порядок map).
//
// Используется для OpenAPI-генерации и для /admin/entities эндпоинта.
func (r *EntityResolver) AllEntities() []string {
	out := make([]string, 0, len(r.entities))
	for name := range r.entities {
		out = append(out, name)
	}
	return out
}