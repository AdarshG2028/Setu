"""Guards against the initial migration drifting from the ORM models.

The migration was hand-written (no Postgres was available to autogenerate
against), so this compares the DDL Alembic emits in offline mode against the
DDL SQLAlchemy generates from Base.metadata. Any missing column, constraint,
or index shows up as a diff.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from backend.models import Base

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _normalize(sql: str) -> str:
    """Collapse whitespace and drop trailing commas/semicolons for comparison."""
    sql = re.sub(r"\s+", " ", sql).strip().rstrip(";").strip()
    return sql.replace("( ", "(").replace(" )", ")").replace(", ", ",")


def _parse_create_tables(sql: str) -> dict[str, str]:
    """Extract {table_name: normalized CREATE TABLE body} from a SQL script."""
    tables: dict[str, str] = {}
    for match in re.finditer(
        r"CREATE TABLE (\w+) \((.*?)\n\)\s*;", sql, re.DOTALL | re.IGNORECASE
    ):
        name = match.group(1)
        if name == "alembic_version":  # Alembic's own bookkeeping table
            continue
        tables[name] = _normalize(match.group(2))
    return tables


def _parse_create_indexes(sql: str) -> dict[str, str]:
    indexes: dict[str, str] = {}
    for match in re.finditer(r"CREATE (?:UNIQUE )?INDEX (\w+) (.*?);", sql, re.DOTALL):
        indexes[match.group(1)] = _normalize(match.group(2))
    return indexes


@pytest.fixture(scope="module")
def migration_sql() -> str:
    """Alembic offline-mode DDL for the full migration history."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


@pytest.fixture(scope="module")
def model_sql() -> str:
    """DDL generated directly from the ORM metadata."""
    dialect = postgresql.dialect()
    parts: list[str] = []
    for table in Base.metadata.sorted_tables:
        parts.append(f"{sa.schema.CreateTable(table).compile(dialect=dialect)};")
        for index in table.indexes:
            parts.append(f"{sa.schema.CreateIndex(index).compile(dialect=dialect)};")
    return "\n".join(parts)


def test_same_tables(migration_sql: str, model_sql: str) -> None:
    migrated = set(_parse_create_tables(migration_sql))
    modeled = set(_parse_create_tables(model_sql))
    assert migrated == modeled, f"table set differs: {migrated ^ modeled}"


def test_same_table_definitions(migration_sql: str, model_sql: str) -> None:
    migrated = _parse_create_tables(migration_sql)
    modeled = _parse_create_tables(model_sql)

    for name, modeled_body in modeled.items():
        migrated_cols = set(migrated[name].split(","))
        modeled_cols = set(modeled_body.split(","))
        assert migrated_cols == modeled_cols, (
            f"{name} differs.\n"
            f"  only in migration: {sorted(migrated_cols - modeled_cols)}\n"
            f"  only in models:    {sorted(modeled_cols - migrated_cols)}"
        )


def test_same_indexes(migration_sql: str, model_sql: str) -> None:
    migrated = _parse_create_indexes(migration_sql)
    modeled = _parse_create_indexes(model_sql)
    assert set(migrated) == set(modeled), (
        f"index set differs: {set(migrated) ^ set(modeled)}"
    )
    for name, definition in modeled.items():
        assert migrated[name] == definition, (
            f"index {name} differs:\n  migration: {migrated[name]}\n"
            f"  models:    {definition}"
        )
