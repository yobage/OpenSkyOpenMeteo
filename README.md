# Real-Time Flight Data Integration Hub with AI

Portfolio project: live flight positions (OpenSky Network) and weather
(Open-Meteo) integrated in real time through a message queue, normalized into
PostgreSQL, with an AI layer for situational summaries and natural-language
querying, and a Streamlit dashboard.

> Work in progress, built phase by phase. This README will gain a full
> architecture diagram, setup guide, and credential instructions in Phase 5.

## Status

- [x] Phase 1 — Ingestion → RabbitMQ
- [ ] Phase 2 — Consumer → enrichment → PostgreSQL
- [ ] Phase 3 — AI layer
- [ ] Phase 4 — Streamlit dashboard
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

RabbitMQ management UI: http://localhost:15672 (guest/guest). Watch the
`flighthub-ingestion` container logs for throughput (`Published N flight(s)
in ...s (... msg/s)`).

### Local dev / tests

```bash
python -m venv .venv && source .venv/Scripts/activate  # or .venv\Scripts\Activate.ps1 on Windows
pip install -e ".[dev]"
pytest
ruff check .
```
