"""Tests for the text-to-SQL SELECT-only safety validator."""

import pytest
from ai.text_to_sql import SQLSafetyError, validate_select_only


def test_allows_simple_select() -> None:
    sql = "SELECT icao24, callsign FROM flights WHERE on_ground = false LIMIT 50"
    assert validate_select_only(sql) == sql


def test_allows_aggregate_select() -> None:
    sql = "SELECT COUNT(*) FROM flights"
    assert validate_select_only(sql) == sql


def test_allows_select_with_cte() -> None:
    sql = "WITH low AS (SELECT * FROM flights WHERE baro_altitude < 500) SELECT * FROM low LIMIT 10"
    assert validate_select_only(sql) == sql


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE flights",
        "DELETE FROM flights WHERE icao24 = '4ca7b3'",
        "UPDATE flights SET callsign = 'x'",
        "INSERT INTO flights (icao24) VALUES ('x')",
        "TRUNCATE flights",
        "ALTER TABLE flights ADD COLUMN foo TEXT",
        "CREATE TABLE evil (id INT)",
    ],
)
def test_rejects_write_and_ddl_statements(sql: str) -> None:
    with pytest.raises(SQLSafetyError):
        validate_select_only(sql)


def test_rejects_stacked_statements() -> None:
    with pytest.raises(SQLSafetyError):
        validate_select_only("SELECT * FROM flights; DROP TABLE flights;")


def test_rejects_select_into() -> None:
    with pytest.raises(SQLSafetyError):
        validate_select_only("SELECT * INTO new_table FROM flights")


def test_rejects_catalog_access() -> None:
    with pytest.raises(SQLSafetyError):
        validate_select_only("SELECT * FROM information_schema.tables")


def test_rejects_dangerous_function_calls() -> None:
    with pytest.raises(SQLSafetyError):
        validate_select_only("SELECT pg_sleep(10)")


def test_rejects_empty_query() -> None:
    with pytest.raises(SQLSafetyError):
        validate_select_only("   ")
