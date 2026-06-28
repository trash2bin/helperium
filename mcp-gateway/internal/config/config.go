// Package config for mcp-gateway
//
// Полноценный loader config.json — читает тот же файл, что и data-service.
// Содержит полные типы Entities, Endpoints, MCPTools, CustomQueries.
package config

import (
	"encoding/json"
	"fmt"
	"os"
)

// Config — корневая структура config.json.
type Config struct {
	Version       int                     `json:"version"`
	DataSource    DataSourceConfig        `json:"data_source"`
	Entities      []Entity                `json:"entities,omitempty"`
	Endpoints     []Endpoint              `json:"endpoints,omitempty"`
	CustomQueries map[string]CustomQuery  `json:"custom_queries,omitempty"`
	Stats         *StatsConfig            `json:"stats,omitempty"`
	MCPTools      []MCPTool               `json:"mcp_tools,omitempty"`
}

// DataSourceConfig — подключение к клиентской БД.
type DataSourceConfig struct {
	Driver string `json:"driver"`
	DSN    string `json:"dsn"`
}

// Entity — доменная сущность (= таблица в клиентской БД).
type Entity struct {
	Name        string        `json:"name"`
	Table       string        `json:"table"`
	IDColumn    string        `json:"id_column"`
	Description string        `json:"description,omitempty"`
	Fields      []EntityField `json:"fields"`
}

// EntityField — поле сущности. name — публичное имя, column — имя колонки.
type EntityField struct {
	Name        string `json:"name"`
	Column      string `json:"column"`
	Type        string `json:"type"`
	Nullable    *bool  `json:"nullable,omitempty"`
	PrimaryKey  *bool  `json:"primary_key,omitempty"`
	Description string `json:"description,omitempty"`
}

// Endpoint — REST endpoint.
type Endpoint struct {
	Method      string         `json:"method"`
	Path        string         `json:"path"`
	Op          string         `json:"op"`
	Entity      string         `json:"entity,omitempty"`
	SearchField string         `json:"search_field,omitempty"`
	QueryParam  string         `json:"query_param,omitempty"`
	QueryID     string         `json:"query_id,omitempty"`
	Params      []EndpointParam `json:"params,omitempty"`
	Description string         `json:"description,omitempty"`
}

// EndpointParam — параметр endpoint'а.
type EndpointParam struct {
	Name        string `json:"name"`
	In          string `json:"in"`
	Type        string `json:"type,omitempty"`
	Required    *bool  `json:"required,omitempty"`
	Description string `json:"description,omitempty"`
}

// CustomQuery — whitelist SQL-запрос с параметрами.
type CustomQuery struct {
	SQL           string                        `json:"sql"`
	Params        []string                      `json:"params,omitempty"`
	ResultMapping map[string]ResultMappingField  `json:"result_mapping"`
	MaxRows       int                           `json:"max_rows"`
	Description   string                        `json:"description,omitempty"`
}

// ResultMappingField — тип колонки результата.
type ResultMappingField struct {
	Type     string `json:"type"`
	Nullable *bool  `json:"nullable,omitempty"`
}

// MCPTool — описание MCP-инструмента.
type MCPTool struct {
	Name        string         `json:"name"`
	Endpoint    string         `json:"endpoint"`
	Description string         `json:"description"`
	Params      []EndpointParam `json:"params,omitempty"`
}

// StatsConfig — конфигурация /stats endpoint'а.
type StatsConfig struct {
	Counters []Counter `json:"counters,omitempty"`
}

// Counter — счётчик для /stats.
type Counter struct {
	Name   string `json:"name"`
	Entity string `json:"entity"`
}

// Load читает config.json целиком.
func Load(path string) (*Config, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("mcp: read config: %w", err)
	}

	var cfg Config
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return nil, fmt.Errorf("mcp: parse config: %w", err)
	}

	return &cfg, nil
}
