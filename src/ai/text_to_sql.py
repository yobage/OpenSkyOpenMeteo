"""Text-to-SQL: answer free-text questions against the flights schema.

The LLM only ever proposes a query; `validate_select_only` is the actual
security boundary. It parses the proposed SQL with sqlparse and rejects
anything that isn't a single, plain SELECT statement — no writes, no DDL,
no stacked statements, no catalog/introspection access. Only a query that
survives validation is ever executed.
"""

from __future__ import annotations

import json
import logging
import re

import sqlparse
from psycopg import Connection
from psycopg.rows import dict_row
from pydantic import BaseModel
from sqlparse.tokens import DDL, DML, Keyword

from ai.llm_provider import LLMProvider

logger = logging.getLogger(__name__)

# Schema handed to the LLM as context. Kept in sync with db/init.sql by hand
# (there are only two tables); if the schema grows, generate this instead.
SCHEMA_CONTEXT = """\
Table flights (current snapshot, one row per aircraft, primary key icao24):
  icao24 TEXT, callsign TEXT, origin_country TEXT,
  longitude DOUBLE PRECISION, latitude DOUBLE PRECISION,
  baro_altitude DOUBLE PRECISION, geo_altitude DOUBLE PRECISION (meters),
  on_ground BOOLEAN, velocity DOUBLE PRECISION (m/s),
  true_track DOUBLE PRECISION (degrees), vertical_rate DOUBLE PRECISION (m/s),
  squawk TEXT, spi BOOLEAN, position_source INTEGER, category INTEGER,
  time_position TIMESTAMPTZ, last_contact TIMESTAMPTZ,
  weather_temperature_c DOUBLE PRECISION, weather_wind_speed_kmh DOUBLE PRECISION,
  weather_wind_direction_deg DOUBLE PRECISION, weather_code INTEGER,
  fetched_at TIMESTAMPTZ, updated_at TIMESTAMPTZ

Table flight_history (append-only, one row per observation over time):
  same columns as flights, plus a surrogate `id BIGSERIAL` primary key and
  `recorded_at TIMESTAMPTZ` instead of `updated_at`; no primary key on icao24
  (an aircraft has many rows over time).
"""

_SQL_SYSTEM_PROMPT = f"""\
You translate natural-language questions into a single read-only PostgreSQL \
SELECT query against this schema:

{SCHEMA_CONTEXT}

Rules:
- Output ONLY the SQL query. No markdown code fences, no explanation.
- SELECT statements only. Never write, alter, or drop anything.
- Prefer the `flights` table for "currently" / "right now" questions, and \
`flight_history` for questions about trends or changes over time.
- Always include a LIMIT clause (200 or fewer) unless the question asks for \
an aggregate (COUNT, AVG, etc.) that returns a single row.
"""

_ANSWER_SYSTEM_PROMPT = (
    "You answer a user's question about air traffic using only the given "
    "SQL query results (JSON rows). Be concise and factual. If the results "
    "are empty, say so plainly rather than guessing."
)

_FORBIDDEN_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CREATE",
    "GRANT", "REVOKE", "CALL", "MERGE", "COPY", "VACUUM", "EXECUTE",
    "INTO", "ATTACH", "REINDEX", "LISTEN", "NOTIFY", "SET", "PREPARE",
}
_FORBIDDEN_PATTERN = re.compile(
    r"\b(pg_sleep|pg_read_file|pg_catalog|information_schema|dblink|pg_terminate_backend)\b",
    re.IGNORECASE,
)
_CODE_FENCE_PATTERN = re.compile(r"^```(?:sql)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


class SQLSafetyError(Exception):
    """Raised when a generated query fails the SELECT-only safety check."""


class TextToSQLResult(BaseModel):
    question: str
    sql: str
    rows: list[dict]
    answer: str


def validate_select_only(sql: str) -> str:
    """Return `sql` stripped/normalized if it is a single safe SELECT, else raise.

    This is the actual security boundary for LLM-generated SQL: it does not
    trust the model's own claim that a query is read-only.
    """
    cleaned = sql.strip()
    if not cleaned:
        raise SQLSafetyError("empty query")

    statements = [s for s in sqlparse.parse(cleaned) if str(s).strip()]
    if len(statements) != 1:
        raise SQLSafetyError(f"exactly one SQL statement is required, got {len(statements)}")

    stmt = statements[0]
    if stmt.get_type() != "SELECT":
        raise SQLSafetyError(f"only SELECT statements are allowed, got: {stmt.get_type()}")

    for token in stmt.flatten():
        if token.ttype in (Keyword, DDL, DML) and token.value.upper() in _FORBIDDEN_KEYWORDS:
            raise SQLSafetyError(f"disallowed keyword in query: {token.value.upper()}")

    if _FORBIDDEN_PATTERN.search(cleaned):
        raise SQLSafetyError("query references a disallowed function or catalog")

    return str(stmt).strip().rstrip(";")


def _ensure_row_limit(sql: str, max_rows: int) -> str:
    if re.search(r"\bLIMIT\s+\d+\b", sql, re.IGNORECASE):
        return sql
    return f"{sql} LIMIT {max_rows}"


def _strip_code_fence(text: str) -> str:
    return _CODE_FENCE_PATTERN.sub("", text.strip()).strip()


def generate_sql(provider: LLMProvider, question: str) -> str:
    """Ask the LLM for a SQL query answering `question`. Not yet validated."""
    raw = provider.complete(question, system=_SQL_SYSTEM_PROMPT)
    return _strip_code_fence(raw)


def answer_question(
    provider: LLMProvider, conn: Connection, question: str, row_limit: int = 200
) -> TextToSQLResult:
    """Generate SQL for `question`, validate it, execute it, and explain the results."""
    raw_sql = generate_sql(provider, question)
    safe_sql = validate_select_only(raw_sql)
    safe_sql = _ensure_row_limit(safe_sql, row_limit)

    logger.info("Executing generated SQL: %s", safe_sql)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(safe_sql)
        rows = cur.fetchall()

    answer_prompt = (
        f"Question: {question}\nSQL used: {safe_sql}\n"
        f"Results (JSON): {json.dumps(rows, default=str)}"
    )
    answer = provider.complete(answer_prompt, system=_ANSWER_SYSTEM_PROMPT)

    return TextToSQLResult(question=question, sql=safe_sql, rows=rows, answer=answer)
