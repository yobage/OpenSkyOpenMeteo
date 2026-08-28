"""Typed models shared across services.

OpenSky's `/states/all` endpoint returns each aircraft state as a plain
index-based JSON array (not an object), documented at
https://openskynetwork.github.io/opensky-api/rest.html#response.
`StateVector.from_array` maps that array into a typed model so the rest of
the codebase never has to remember magic indices.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

# Index of each field within the raw OpenSky state-vector array.
_ICAO24 = 0
_CALLSIGN = 1
_ORIGIN_COUNTRY = 2
_TIME_POSITION = 3
_LAST_CONTACT = 4
_LONGITUDE = 5
_LATITUDE = 6
_BARO_ALTITUDE = 7
_ON_GROUND = 8
_VELOCITY = 9
_TRUE_TRACK = 10
_VERTICAL_RATE = 11
_SENSORS = 12
_GEO_ALTITUDE = 13
_SQUAWK = 14
_SPI = 15
_POSITION_SOURCE = 16
_CATEGORY = 17
_MIN_LENGTH = 17  # arrays are at least this long; `category` (17) is optional


class StateVector(BaseModel):
    """A single aircraft state, as parsed from an OpenSky state-vector array."""

    icao24: str
    callsign: str | None = None
    origin_country: str
    time_position: int | None = None
    last_contact: int | None = None
    longitude: float | None = None
    latitude: float | None = None
    baro_altitude: float | None = None
    on_ground: bool = False
    velocity: float | None = None
    true_track: float | None = None
    vertical_rate: float | None = None
    sensors: list[int] | None = None
    geo_altitude: float | None = None
    squawk: str | None = None
    spi: bool = False
    position_source: int | None = None
    category: int | None = None

    @classmethod
    def from_array(cls, arr: list[Any]) -> StateVector:
        """Map a raw index-based OpenSky state-vector array into a StateVector.

        Raises ValueError if the array is too short to be a valid state vector.
        """
        if len(arr) < _MIN_LENGTH:
            raise ValueError(
                f"state vector array too short: expected >= {_MIN_LENGTH} fields, got {len(arr)}"
            )

        callsign = arr[_CALLSIGN]
        return cls(
            icao24=arr[_ICAO24],
            callsign=callsign.strip() if isinstance(callsign, str) else None,
            origin_country=arr[_ORIGIN_COUNTRY],
            time_position=arr[_TIME_POSITION],
            last_contact=arr[_LAST_CONTACT],
            longitude=arr[_LONGITUDE],
            latitude=arr[_LATITUDE],
            baro_altitude=arr[_BARO_ALTITUDE],
            on_ground=bool(arr[_ON_GROUND]),
            velocity=arr[_VELOCITY],
            true_track=arr[_TRUE_TRACK],
            vertical_rate=arr[_VERTICAL_RATE],
            sensors=arr[_SENSORS],
            geo_altitude=arr[_GEO_ALTITUDE],
            squawk=arr[_SQUAWK],
            spi=bool(arr[_SPI]),
            position_source=arr[_POSITION_SOURCE],
            category=arr[_CATEGORY] if len(arr) > _CATEGORY else None,
        )


class FlightMessage(BaseModel):
    """The JSON payload published to RabbitMQ for each tracked aircraft."""

    state: StateVector
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
