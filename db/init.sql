-- Schema for the flight data integration hub.
-- Applied automatically by the official Postgres image on first container
-- startup (mounted into /docker-entrypoint-initdb.d).

-- Current snapshot of each tracked aircraft, keyed by ICAO24 address.
-- The consumer upserts into this table on every message.
CREATE TABLE IF NOT EXISTS flights (
    icao24                      TEXT PRIMARY KEY,
    callsign                    TEXT,
    origin_country               TEXT NOT NULL,
    longitude                   DOUBLE PRECISION,
    latitude                    DOUBLE PRECISION,
    baro_altitude                DOUBLE PRECISION,
    geo_altitude                 DOUBLE PRECISION,
    on_ground                   BOOLEAN NOT NULL DEFAULT FALSE,
    velocity                    DOUBLE PRECISION,
    true_track                   DOUBLE PRECISION,
    vertical_rate                DOUBLE PRECISION,
    squawk                      TEXT,
    spi                         BOOLEAN NOT NULL DEFAULT FALSE,
    position_source               INTEGER,
    category                    INTEGER,
    time_position                 TIMESTAMPTZ,
    last_contact                 TIMESTAMPTZ,
    weather_temperature_c         DOUBLE PRECISION,
    weather_wind_speed_kmh         DOUBLE PRECISION,
    weather_wind_direction_deg     DOUBLE PRECISION,
    weather_code                 INTEGER,
    fetched_at                  TIMESTAMPTZ NOT NULL,
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only history of every enriched observation, for tracking flights
-- over time (trajectories, anomaly detection, situational summaries).
CREATE TABLE IF NOT EXISTS flight_history (
    id                          BIGSERIAL PRIMARY KEY,
    icao24                      TEXT NOT NULL,
    callsign                    TEXT,
    origin_country               TEXT NOT NULL,
    longitude                   DOUBLE PRECISION,
    latitude                    DOUBLE PRECISION,
    baro_altitude                DOUBLE PRECISION,
    geo_altitude                 DOUBLE PRECISION,
    on_ground                   BOOLEAN NOT NULL DEFAULT FALSE,
    velocity                    DOUBLE PRECISION,
    true_track                   DOUBLE PRECISION,
    vertical_rate                DOUBLE PRECISION,
    squawk                      TEXT,
    spi                         BOOLEAN NOT NULL DEFAULT FALSE,
    position_source               INTEGER,
    category                    INTEGER,
    time_position                 TIMESTAMPTZ,
    last_contact                 TIMESTAMPTZ,
    weather_temperature_c         DOUBLE PRECISION,
    weather_wind_speed_kmh         DOUBLE PRECISION,
    weather_wind_direction_deg     DOUBLE PRECISION,
    weather_code                 INTEGER,
    fetched_at                  TIMESTAMPTZ NOT NULL,
    recorded_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Geo/area lookups (e.g. "flights currently over the north") and freshness
-- filtering on the current snapshot.
CREATE INDEX IF NOT EXISTS idx_flights_lat_lon ON flights (latitude, longitude);
CREATE INDEX IF NOT EXISTS idx_flights_updated_at ON flights (updated_at);

-- Per-aircraft trajectory lookups over time on the history table.
CREATE INDEX IF NOT EXISTS idx_history_icao24_fetched_at ON flight_history (icao24, fetched_at);
CREATE INDEX IF NOT EXISTS idx_history_fetched_at ON flight_history (fetched_at);
