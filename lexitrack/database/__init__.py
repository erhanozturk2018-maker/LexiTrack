"""SQLite persistence primitives."""

from .connection import SCHEMA_VERSION, Database

__all__ = ["SCHEMA_VERSION", "Database"]
