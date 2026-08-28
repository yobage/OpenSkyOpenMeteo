"""Streamlit dashboard: live flight map, weather, AI summary, and Q&A.

Every DB call opens and closes its own short-lived psycopg connection rather
than sharing one cached connection across Streamlit sessions/threads — a
plain synchronous connection isn't safe for concurrent use, and this app is
demo-scale, so the extra connect overhead is a fine trade for correctness.

The live map/table panel auto-refreshes on a timer via `st.fragment`. The
LLM-backed panels (summary, anomalies, Q&A) are button-triggered instead of
auto-refreshing, so a free-tier API quota isn't burned on every tick.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
import pandas as pd
import psycopg
import pydeck as pdk
import streamlit as st
from ai.anomalies import detect_anomalies, detect_holding_pattern, explain_anomalies
from ai.llm_provider import LLMProvider, get_llm_provider
from ai.summary import generate_situational_summary
from ai.text_to_sql import SQLSafetyError, answer_question
from common.config import Settings, get_settings
from common.logging import configure_logging
from common.models import EnrichedFlight
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

_WEATHER_CODE_DESCRIPTIONS = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    95: "Thunderstorm", 96: "Thunderstorm, slight hail", 99: "Thunderstorm, heavy hail",
}  # fmt: skip

# Altitude (meters) -> RGB gradient stops for map coloring: blue (low/ground)
# through teal and yellow to red (high).
_ALTITUDE_COLOR_STOPS: list[tuple[float, tuple[int, int, int]]] = [
    (0.0, (33, 102, 172)),
    (3000.0, (103, 194, 165)),
    (6000.0, (255, 224, 138)),
    (9000.0, (252, 141, 89)),
    (12000.0, (178, 24, 43)),
]

HISTORY_LOOKBACK_MINUTES = 20


# Loaded once at script top level (Streamlit re-executes this whole file on
# every full rerun, so this is re-read from the environment each time, but
# that's cheap and lets the auto-refresh fragment below use the configured
# interval directly in its decorator).
_settings = get_settings()
configure_logging(_settings.log_level)


@st.cache_resource
def _http_client() -> httpx.Client:
    return httpx.Client(timeout=30.0)


def _get_provider(settings: Settings, http_client: httpx.Client) -> LLMProvider | None:
    try:
        return get_llm_provider(settings, http_client)
    except ValueError:
        return None


def _weather_description(code: int | None) -> str:
    if code is None:
        return "Unknown"
    return _WEATHER_CODE_DESCRIPTIONS.get(code, f"WMO code {code}")


def _altitude_color(altitude_m: float | None) -> list[int]:
    if altitude_m is None:
        return [140, 140, 140, 160]
    alt = max(0.0, min(altitude_m, _ALTITUDE_COLOR_STOPS[-1][0]))
    for (a0, c0), (a1, c1) in zip(_ALTITUDE_COLOR_STOPS, _ALTITUDE_COLOR_STOPS[1:], strict=False):
        if a0 <= alt <= a1:
            t = (alt - a0) / (a1 - a0)
            rgb = [round(c0[i] + t * (c1[i] - c0[i])) for i in range(3)]
            return [*rgb, 200]
    return [*_ALTITUDE_COLOR_STOPS[-1][1], 200]


def _fetch_current_flights(dsn: str) -> list[EnrichedFlight]:
    with psycopg.connect(dsn) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM flights ORDER BY updated_at DESC")
        rows = cur.fetchall()
    return [EnrichedFlight.model_validate(row) for row in rows]


def _fetch_recent_history(dsn: str, minutes: int) -> dict[str, list[EnrichedFlight]]:
    with psycopg.connect(dsn) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM flight_history "
            "WHERE fetched_at > now() - (%(minutes)s || ' minutes')::interval "
            "ORDER BY icao24, fetched_at",
            {"minutes": minutes},
        )
        rows = cur.fetchall()

    grouped: dict[str, list[EnrichedFlight]] = {}
    for row in rows:
        flight = EnrichedFlight.model_validate(row)
        grouped.setdefault(flight.icao24, []).append(flight)
    return grouped


def _flights_dataframe(flights: list[EnrichedFlight]) -> pd.DataFrame:
    records = [
        {
            "icao24": f.icao24,
            "callsign": f.callsign or "",
            "origin_country": f.origin_country,
            "latitude": f.latitude,
            "longitude": f.longitude,
            "altitude_m": f.baro_altitude,
            "on_ground": f.on_ground,
            "velocity_ms": f.velocity,
            "vertical_rate_ms": f.vertical_rate,
            "weather_temp_c": f.weather_temperature_c,
            "weather_wind_kmh": f.weather_wind_speed_kmh,
            "weather": _weather_description(f.weather_code),
            "fetched_at": f.fetched_at,
            "color": _altitude_color(f.baro_altitude),
        }
        for f in flights
    ]
    return pd.DataFrame.from_records(records)


def _render_map(df: pd.DataFrame, settings: Settings) -> None:
    plottable = df.dropna(subset=["latitude", "longitude"])
    if plottable.empty:
        st.info("No aircraft with a known position to display.")
        return

    layer = pdk.Layer(
        "ScatterplotLayer",
        data=plottable,
        get_position=["longitude", "latitude"],
        get_fill_color="color",
        get_radius=2500,
        pickable=True,
        opacity=0.8,
    )
    view_state = pdk.ViewState(
        latitude=(settings.opensky_lamin + settings.opensky_lamax) / 2,
        longitude=(settings.opensky_lomin + settings.opensky_lomax) / 2,
        zoom=6.3,
    )
    tooltip = {
        "html": (
            "<b>{callsign}</b> ({icao24})<br/>"
            "Altitude: {altitude_m} m &nbsp; Velocity: {velocity_ms} m/s<br/>"
            "Weather: {weather}, {weather_temp_c}&deg;C, wind {weather_wind_kmh} km/h"
        ),
        "style": {"backgroundColor": "steelblue", "color": "white"},
    }
    st.pydeck_chart(
        pdk.Deck(layers=[layer], initial_view_state=view_state, tooltip=tooltip, map_style=None)
    )


@st.fragment(run_every=_settings.dashboard_refresh_seconds)
def _live_panel(dsn: str, settings: Settings) -> None:
    flights = _fetch_current_flights(dsn)
    df = _flights_dataframe(flights)

    st.caption(
        f"Last refreshed {datetime.now(UTC):%H:%M:%S} UTC — "
        f"{len(flights)} aircraft tracked (auto-refreshes every "
        f"{int(settings.dashboard_refresh_seconds)}s)"
    )
    _render_map(df, settings)

    with st.expander(f"Flight + weather table ({len(df)} rows)", expanded=False):
        st.dataframe(df.drop(columns=["color"]), use_container_width=True)


def _render_summary_panel(provider: LLMProvider | None, dsn: str) -> None:
    st.subheader("AI situational summary")
    if provider is None:
        st.info("Set GEMINI_API_KEY or GROQ_API_KEY in .env to enable AI features.")
        return

    if st.button("Generate summary"):
        flights = _fetch_current_flights(dsn)
        with st.spinner("Asking the LLM..."):
            summary = generate_situational_summary(provider, flights)
        st.write(summary)


def _render_anomalies_panel(provider: LLMProvider | None, dsn: str) -> None:
    st.subheader("Anomaly detection")
    if st.button("Detect anomalies"):
        flights = _fetch_current_flights(dsn)
        flags = detect_anomalies(flights)

        history = _fetch_recent_history(dsn, HISTORY_LOOKBACK_MINUTES)
        for icao24, points in history.items():
            callsign = points[0].callsign if points else None
            holding = detect_holding_pattern(icao24, callsign, points)
            if holding is not None:
                flags.append(holding)

        if not flags:
            st.success("No anomalies detected in the current snapshot.")
            return

        st.dataframe(pd.DataFrame([f.model_dump() for f in flags]), use_container_width=True)

        if provider is not None:
            with st.spinner("Asking the LLM to explain..."):
                explanation = explain_anomalies(provider, flags)
            st.write(explanation)


def _render_qa_panel(provider: LLMProvider | None, dsn: str) -> None:
    st.subheader("Ask a question")
    if provider is None:
        st.info("Set GEMINI_API_KEY or GROQ_API_KEY in .env to enable AI features.")
        return

    question = st.text_input(
        "Free-text question about current or recent air traffic",
        placeholder="e.g. How many aircraft are currently below 1000 meters?",
    )
    if st.button("Ask") and question:
        try:
            with psycopg.connect(dsn) as conn, st.spinner("Generating and running SQL..."):
                result = answer_question(provider, conn, question)
        except SQLSafetyError as exc:
            st.error(f"Generated query was rejected for safety: {exc}")
            return
        except Exception:
            logger.exception("Text-to-SQL request failed")
            st.error("Something went wrong answering that question. Check the logs.")
            return

        st.write(result.answer)
        with st.expander("SQL used"):
            st.code(result.sql, language="sql")
        st.dataframe(pd.DataFrame(result.rows), use_container_width=True)


def main() -> None:
    st.set_page_config(page_title="Flight Data Integration Hub", page_icon="✈️", layout="wide")
    settings = _settings
    http_client = _http_client()
    provider = _get_provider(settings, http_client)

    st.title("✈️ Real-Time Flight Data Integration Hub")

    with st.sidebar:
        st.header("Configuration")
        st.write(f"**LLM provider:** {settings.llm_provider}")
        st.write("AI features: " + ("enabled" if provider else "disabled (no API key)"))
        st.write(
            f"**Bounding box:** ({settings.opensky_lamin}, {settings.opensky_lomin}) - "
            f"({settings.opensky_lamax}, {settings.opensky_lomax})"
        )

    try:
        _live_panel(settings.postgres_dsn, settings)
    except psycopg.OperationalError:
        st.error(
            "Could not connect to PostgreSQL. Make sure the database is running "
            "and the ingestion/consumer services have had a chance to publish data."
        )
        st.stop()

    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        _render_summary_panel(provider, settings.postgres_dsn)
    with col2:
        _render_anomalies_panel(provider, settings.postgres_dsn)

    st.divider()
    _render_qa_panel(provider, settings.postgres_dsn)


if __name__ == "__main__":
    main()
