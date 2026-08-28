# Real-Time Flight Data Integration Hub with AI

Portfolio project: live flight positions (OpenSky Network) and weather
(Open-Meteo) integrated in real time through a message queue, normalized into
PostgreSQL, with an AI layer for situational summaries and natural-language
querying, and a Streamlit dashboard.

> Work in progress, built phase by phase. This README will gain a full
> architecture diagram, setup guide, and credential instructions in Phase 5.

## Status

- [x] Phase 1 — Ingestion → RabbitMQ
- [x] Phase 2 — Consumer → enrichment → PostgreSQL
- [x] Phase 3 — AI layer
- [x] Phase 4 — Streamlit dashboard
- [ ] Phase 5 — Packaging (docker-compose, CI, docs)

## Phase 1: Ingestion

`src/ingestion` authenticates to OpenSky (OAuth2 client-credentials, with
automatic token refresh; falls back to anonymous access if no credentials are
configured), polls `/states/all` for a bounding box around Israel every
`POLL_INTERVAL_SECONDS`, parses the index-based state-vector arrays into a
typed `StateVector` model, and publishes each aircraft as a JSON message to a
durable RabbitMQ exchange.

### Run it

```bash
cp .env.example .env   # optionally fill in OpenSky client_id/client_secret
docker compose up --build
```

RabbitMQ management UI: http://localhost:15672 (guest/guest). Dashboard:
http://localhost:8501. Watch the `flighthub-ingestion` container logs for
throughput (`Published N flight(s) in ...s (... msg/s)`).

## Phase 2: Consumer, enrichment, storage

`src/consumer` reads flight messages from RabbitMQ, looks up current weather
from Open-Meteo for each position (cached per lat/lon grid cell to avoid
redundant calls for nearby aircraft), and upserts the normalized result into
PostgreSQL: a `flights` snapshot table (one row per icao24) plus an
append-only `flight_history` table. Schema and indexes: `db/init.sql`.

## Phase 3: AI layer

`src/ai` is a provider-agnostic module (switch providers with `LLM_PROVIDER`,
no code change) offering three things the dashboard calls into:

- **Situational summaries** (`summary.py`) — stats are computed
  deterministically in Python, then handed to the LLM to narrate in plain
  English, so it can't invent numbers.
- **Text-to-SQL** (`text_to_sql.py`) — turns a free-text question into SQL
  against the flights schema. Every generated query is parsed with `sqlparse`
  and rejected unless it's a single, plain `SELECT` with no writes, DDL,
  catalog access, or stacked statements — the LLM's own claim that a query is
  safe is never trusted.
- **Anomaly detection** (`anomalies.py`) — flags (unusually low altitude,
  rapid climb/descent, possible holding patterns) are found with deterministic
  thresholds/geometry, not the LLM; the LLM only explains flags after the
  fact.

## Phase 4: Dashboard

`src/dashboard/app.py` is a Streamlit app at http://localhost:8501:

- A map of currently tracked flights (`pydeck`), colored on a blue-to-red
  gradient by altitude, with a tooltip showing callsign, altitude, velocity,
  and weather at that position.
- A flight + weather data table, and both auto-refresh on
  `DASHBOARD_REFRESH_SECONDS` via `st.fragment` — this part just re-reads
  Postgres, so refreshing it costs nothing.
- An AI situational-summary panel and an anomaly-detection panel (both call
  into `src/ai`), plus a free-text question box wired to text-to-SQL. These
  are button-triggered rather than auto-refreshing, so they don't burn
  free-tier LLM quota on every tick.
- If no `GEMINI_API_KEY`/`GROQ_API_KEY` is set, the map/table still work; the
  AI panels show a note instead of failing.

### Local dev / tests

```bash
python -m venv .venv && source .venv/Scripts/activate  # or .venv\Scripts\Activate.ps1 on Windows
pip install -e ".[dev]"
pytest
ruff check .
```
