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
