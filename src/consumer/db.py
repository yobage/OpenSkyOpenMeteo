"""PostgreSQL persistence for enriched flights.

Every message upserts the current snapshot in `flights` (keyed by icao24)
and appends one row to `flight_history`, so the schema supports both "where
is everything right now" and "how has this aircraft moved over time"
queries. Schema lives in db/init.sql, applied by the Postgres container on
first startup.
"""

from __future__ import annotations

import logging
import threading

import psycopg
from common.models import EnrichedFlight
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_UPSERT_FLIGHT_SQL = """
INSERT INTO flights (
    icao24, callsign, origin_country, longitude, latitude, baro_altitude, geo_altitude,
    on_ground, velocity, true_track, vertical_rate, squawk, spi, position_source, category,
    time_position, last_contact, weather_temperature_c, weather_wind_speed_kmh,
    weather_wind_direction_deg, weather_code, fetched_at
) VALUES (
    %(icao24)s, %(callsign)s, %(origin_country)s, %(longitude)s, %(latitude)s, %(baro_altitude)s,
    %(geo_altitude)s, %(on_ground)s, %(velocity)s, %(true_track)s, %(vertical_rate)s, %(squawk)s,
    %(spi)s, %(position_source)s, %(category)s, %(time_position)s, %(last_contact)s,
    %(weather_temperature_c)s, %(weather_wind_speed_kmh)s, %(weather_wind_direction_deg)s,
    %(weather_code)s, %(fetched_at)s
)
ON CONFLICT (icao24) DO UPDATE SET
    callsign = EXCLUDED.callsign,
    origin_country = EXCLUDED.origin_country,
    longitude = EXCLUDED.longitude,
    latitude = EXCLUDED.latitude,
    baro_altitude = EXCLUDED.baro_altitude,
    geo_altitude = EXCLUDED.geo_altitude,
    on_ground = EXCLUDED.on_ground,
    velocity = EXCLUDED.velocity,
    true_track = EXCLUDED.true_track,
    vertical_rate = EXCLUDED.vertical_rate,
    squawk = EXCLUDED.squawk,
    spi = EXCLUDED.spi,
    position_source = EXCLUDED.position_source,
    category = EXCLUDED.category,
    time_position = EXCLUDED.time_position,
    last_contact = EXCLUDED.last_contact,
    weather_temperature_c = EXCLUDED.weather_temperature_c,
    weather_wind_speed_kmh = EXCLUDED.weather_wind_speed_kmh,
    weather_wind_direction_deg = EXCLUDED.weather_wind_direction_deg,
    weather_code = EXCLUDED.weather_code,
    fetched_at = EXCLUDED.fetched_at,
    updated_at = now();
"""

_INSERT_HISTORY_SQL = """
INSERT INTO flight_history (
    icao24, callsign, origin_country, longitude, latitude, baro_altitude, geo_altitude,
    on_ground, velocity, true_track, vertical_rate, squawk, spi, position_source, category,
    time_position, last_contact, weather_temperature_c, weather_wind_speed_kmh,
    weather_wind_direction_deg, weather_code, fetched_at
) VALUES (
    %(icao24)s, %(callsign)s, %(origin_country)s, %(longitude)s, %(latitude)s, %(baro_altitude)s,
    %(geo_altitude)s, %(on_ground)s, %(velocity)s, %(true_track)s, %(vertical_rate)s, %(squawk)s,
    %(spi)s, %(position_source)s, %(category)s, %(time_position)s, %(last_contact)s,
    %(weather_temperature_c)s, %(weather_wind_speed_kmh)s, %(weather_wind_direction_deg)s,
    %(weather_code)s, %(fetched_at)s
);
"""


class FlightRepository:
    """Upserts enriched flights into PostgreSQL.

    Called from multiple consumer worker threads sharing one connection; a
    lock keeps each upsert-and-history-insert pair atomic and serialized.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn: psycopg.Connection | None = None
        self._lock = threading.Lock()

    @retry(
        retry=retry_if_exception_type(psycopg.OperationalError),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(10),
        reraise=True,
    )
    def connect(self) -> None:
        logger.info("Connecting to PostgreSQL")
        self._conn = psycopg.connect(self._dsn)
        logger.info("Connected to PostgreSQL")

    def upsert_flight(self, flight: EnrichedFlight) -> None:
        """Update the current snapshot and append a history row, atomically."""
        if self._conn is None:
            raise RuntimeError("repository not connected; call connect() first")
        params = flight.model_dump()
        with self._lock, self._conn.cursor() as cur:
            cur.execute(_UPSERT_FLIGHT_SQL, params)
            cur.execute(_INSERT_HISTORY_SQL, params)
            self._conn.commit()

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
            logger.info("PostgreSQL connection closed")
