"""Column types that work on both Postgres and SQLite.

The app runs on Postgres, but the test suite uses an in-memory SQLite database
so it doesn't need a running server. Postgres-only types (UUID, JSONB) break on
SQLite, so these decorators fall back to portable equivalents when the dialect
isn't Postgres.
"""

import uuid

from sqlalchemy import CHAR, JSON
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.types import TypeDecorator


class GUID(TypeDecorator):
    """UUID on Postgres, CHAR(36) everywhere else."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(str(value))
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


# JSONB on Postgres, plain JSON on SQLite.
JSONColumn = JSON().with_variant(JSONB, "postgresql")
