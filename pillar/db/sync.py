"""
Pillar Zero-Migration Schema Sync.

In dev mode, Pillar compares each Pydantic model registered as a DB
table against the actual SQLite schema and automatically applies SAFE,
additive migrations:

  * ADD COLUMN for new fields
  * No destructive changes (DROP COLUMN, type changes) — always explicit

Usage::

    from pillar.db.sync import SchemaSync
    from pydantic import BaseModel

    class User(BaseModel):
        id:    int
        name:  str
        email: str
        bio:   str = ""   # NEW field — will be auto-added to the DB

    sync = SchemaSync(db)
    sync.ensure_table("users", User)
    # → runs: ALTER TABLE users ADD COLUMN bio TEXT NOT NULL DEFAULT ''

Compile-time contract validation::

    sync.validate_contract("users", User)
    # Raises PillarContractError if any field in User is MISSING from DB
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel

from ..exceptions import PillarContractError


# ──────────────────────────────────────────────────────────────────────
# Type mapping: Python → SQLite
# ──────────────────────────────────────────────────────────────────────

_PY_TO_SQL: Dict[Any, str] = {
    int:   "INTEGER",
    float: "REAL",
    bool:  "INTEGER",
    bytes: "BLOB",
    str:   "TEXT",
}


def _sql_type(annotation: Any) -> str:
    """Return the SQLite column type for a Python annotation."""
    import typing
    origin = getattr(annotation, "__origin__", None)
    args   = getattr(annotation, "__args__", ()) or ()

    # Optional[X] → unwrap
    if origin is typing.Union:
        non_none = [a for a in args if a is not type(None)]
        if non_none:
            return _sql_type(non_none[0])

    # List / dict → JSON text
    if origin in (list, dict):
        return "TEXT"

    return _PY_TO_SQL.get(annotation, "TEXT")


def _default_value(field_default: Any, sql_type: str) -> str:
    """Return an SQL DEFAULT clause fragment."""
    if field_default is None:
        return "NULL"
    if isinstance(field_default, bool):
        return "1" if field_default else "0"
    if isinstance(field_default, (int, float)):
        return str(field_default)
    if isinstance(field_default, str):
        return f"'{field_default}'"
    return "''"


# ──────────────────────────────────────────────────────────────────────
# SchemaSync
# ──────────────────────────────────────────────────────────────────────

class SchemaSync:
    """
    Zero-migration schema synchroniser for SQLite.

    Compares Pydantic models to the live database schema and applies
    safe, additive migrations automatically in development mode.
    """

    def __init__(self, db: Any) -> None:
        """
        *db* must be a Pillar ``Database`` instance (or any object with
        ``query_all(sql)`` and ``execute(sql)`` methods).
        """
        self._db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ensure_table(self, table_name: str, model: Type[BaseModel]) -> List[str]:
        """
        Create the table if it doesn't exist, then add any missing columns.

        Returns a list of SQL statements that were executed.
        """
        executed: List[str] = []

        columns = self._get_columns(table_name)

        if not columns:
            # Table does not exist → CREATE
            sql = self._create_sql(table_name, model)
            self._db.execute(sql)
            executed.append(sql)
            return executed

        # Table exists → ADD missing columns only
        schema = model.model_json_schema()
        properties = schema.get("properties", {})
        required   = set(schema.get("required", []))

        for field_name, field_info in properties.items():
            if field_name in columns:
                continue
            # Field is missing — add it
            sql_type = self._json_schema_to_sql(field_info)
            nullable = field_name not in required
            default  = field_info.get("default")
            if default is None and nullable:
                alter = f"ALTER TABLE {table_name} ADD COLUMN {field_name} {sql_type}"
            elif default is not None:
                quoted = _default_value(default, sql_type)
                alter  = (
                    f"ALTER TABLE {table_name} ADD COLUMN {field_name} {sql_type}"
                    f" NOT NULL DEFAULT {quoted}"
                )
            else:
                alter = (
                    f"ALTER TABLE {table_name} ADD COLUMN {field_name} {sql_type}"
                    f" NOT NULL DEFAULT ''"
                )
            self._db.execute(alter)
            executed.append(alter)

        return executed

    def validate_contract(self, table_name: str, model: Type[BaseModel]) -> None:
        """
        Compile-time contract check: every non-optional field in *model*
        must exist in *table_name*.

        Raises ``PillarContractError`` on mismatch.
        Runs at server startup so contract violations are caught before
        the first request, not mid-flight.
        """
        columns = self._get_columns(table_name)
        if not columns:
            raise PillarContractError(
                f"Contract violation: table '{table_name}' does not exist, "
                f"but model '{model.__name__}' expects it."
            )

        schema   = model.model_json_schema()
        required = set(schema.get("required", []))

        missing = [f for f in required if f not in columns]
        if missing:
            raise PillarContractError(
                f"\n"
                f"  Model    : {model.__name__}\n"
                f"  Table    : {table_name}\n"
                f"  Missing  : {missing}\n"
                f"\n"
                f"  The database schema is missing required fields from the Pydantic model.\n"
                f"  Run SchemaSync.ensure_table('{table_name}', {model.__name__}) in dev mode\n"
                f"  or apply a manual migration before deploying."
            )

    def diff(self, table_name: str, model: Type[BaseModel]) -> Dict[str, List[str]]:
        """
        Return a structured diff between model fields and DB columns.

        Returns ``{"missing_in_db": [...], "extra_in_db": [...]}``
        """
        columns = set(self._get_columns(table_name).keys())
        schema  = model.model_json_schema()
        fields  = set(schema.get("properties", {}).keys())
        return {
            "missing_in_db":    sorted(fields - columns),
            "extra_in_db":      sorted(columns - fields),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_columns(self, table_name: str) -> Dict[str, str]:
        """Return {column_name: type} for *table_name*, or {} if missing."""
        try:
            rows = self._db.query_all(f"PRAGMA table_info({table_name})")
            return {r["name"]: r["type"] for r in rows}
        except Exception:
            return {}

    def _create_sql(self, table_name: str, model: Type[BaseModel]) -> str:
        schema     = model.model_json_schema()
        properties = schema.get("properties", {})
        required   = set(schema.get("required", []))

        cols: List[str] = []
        pk_set = False

        for field_name, field_info in properties.items():
            sql_type = self._json_schema_to_sql(field_info)
            nullable = field_name not in required

            # Heuristic: first integer non-optional field named 'id' → PRIMARY KEY
            if field_name == "id" and sql_type == "INTEGER" and not pk_set:
                cols.append(f"{field_name} INTEGER PRIMARY KEY AUTOINCREMENT")
                pk_set = True
                continue

            default = field_info.get("default")
            if nullable:
                cols.append(f"{field_name} {sql_type}")
            elif default is not None:
                cols.append(
                    f"{field_name} {sql_type} NOT NULL DEFAULT {_default_value(default, sql_type)}"
                )
            else:
                cols.append(f"{field_name} {sql_type} NOT NULL")

        return f"CREATE TABLE IF NOT EXISTS {table_name} ({', '.join(cols)})"

    @staticmethod
    def _json_schema_to_sql(field_info: dict) -> str:
        t = field_info.get("type")
        if t == "integer":
            return "INTEGER"
        if t == "number":
            return "REAL"
        if t == "boolean":
            return "INTEGER"
        if t in ("array", "object"):
            return "TEXT"
        return "TEXT"
