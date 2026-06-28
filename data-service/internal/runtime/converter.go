package runtime

import "github.com/agent-tutor/data-service/internal/config"

// ConfigToEntities конвертирует config.Entity в runtime.Entity.
func ConfigToEntities(cfgEntities []config.Entity) []Entity {
	out := make([]Entity, 0, len(cfgEntities))
	for _, e := range cfgEntities {
		fields := make([]EntityField, len(e.Fields))
		for i, f := range e.Fields {
			nullable := false
			if f.Nullable != nil {
				nullable = *f.Nullable
			}
			pk := false
			if f.PrimaryKey != nil {
				pk = *f.PrimaryKey
			}
			fields[i] = EntityField{
				Name:       f.Name,
				Column:     f.Column,
				Type:       string(f.Type),
				Nullable:   nullable,
				PrimaryKey: pk,
			}
		}
		out = append(out, Entity{
			Name:     e.Name,
			Table:    e.Table,
			IDColumn: e.IDColumn,
			Fields:   fields,
		})
	}
	return out
}

// ConfigToCustomQueries конвертирует map[string]config.CustomQuery в runtime.CustomQuery.
func ConfigToCustomQueries(cfgQueries map[string]config.CustomQuery) map[string]CustomQuery {
	out := make(map[string]CustomQuery, len(cfgQueries))
	for k, v := range cfgQueries {
		rm := make(map[string]ResultMappingField, len(v.ResultMapping))
		for rk, rv := range v.ResultMapping {
			nullable := false
			if rv.Nullable != nil {
				nullable = *rv.Nullable
			}
			rm[rk] = ResultMappingField{
				Type:     string(rv.Type),
				Nullable: nullable,
			}
		}
		out[k] = CustomQuery{
			SQL:           v.SQL,
			Params:        v.Params,
			ResultMapping: rm,
			MaxRows:       v.MaxRows,
		}
	}
	return out
}

// ConfigToEndpointParams конвертирует []config.EndpointParam в config.EndpointParam
// (identity — оставляем как есть, так как хендлеры используют config.EndpointParam).