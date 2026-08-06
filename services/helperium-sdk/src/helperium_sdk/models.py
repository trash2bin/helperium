"""Generic entity model for data-service responses.

Replaces the deprecated typed contracts (Student, Teacher, etc.).
Data-service returns schemaless JSON; Entity captures it with
optional validation via `extra` mode.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Entity(BaseModel):
    """A record from data-service.

    ``id`` is the only guaranteed field. All other fields are accessible
    as attributes (``entity.name``, ``entity.full_name``, etc.) without
    compile-time validation — the data-service owns the schema.
    """

    model_config = ConfigDict(extra="allow", frozen=False)

    id: str
