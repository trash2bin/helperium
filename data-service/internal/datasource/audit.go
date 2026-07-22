package datasource

import "context"

// AuditRecorder — интерфейс для записи аудита вызовов LLM-инструментов.
//
// По умолчанию используется NoopAuditRecorder — ничего не делает.
// Прод может подставить имплементацию, пишущую в БД/логгер/кафку.
type AuditRecorder interface {
	// RecordToolCall записывает один вызов LLM-инструмента.
	RecordToolCall(ctx context.Context, call *ToolCallRecord) error
}

// ToolCallRecord — запись одного вызова LLM-инструмента.
type ToolCallRecord struct {
	ToolName    string
	Entity      string
	TenantID    string
	Params      map[string]any
	RowsReturned int
	DurationMs  int64
	Error       string
}

// NoopAuditRecorder — пустая имплементация AuditRecorder.
// Используется по умолчанию, ничего не делает.
type NoopAuditRecorder struct{}

func (n *NoopAuditRecorder) RecordToolCall(_ context.Context, _ *ToolCallRecord) error {
	return nil
}

// GlobalAuditRecorder — глобальный экземпляр для доступа из DataSource.
// Замена на прод-имплементацию через SetAuditRecorder.
var GlobalAuditRecorder AuditRecorder = &NoopAuditRecorder{}

// SetAuditRecorder устанавливает глобальный AuditRecorder.
func SetAuditRecorder(r AuditRecorder) {
	if r == nil {
		GlobalAuditRecorder = &NoopAuditRecorder{}
		return
	}
	GlobalAuditRecorder = r
}
