"""Tests for OpenSky state-vector array -> StateVector parsing."""

import pytest
from common.models import StateVector

# A realistic raw state vector as returned by OpenSky /states/all, with the
# `category` field (index 17) present.
FULL_STATE = [
    "4ca7b3",       # icao24
    "ELY001  ",     # callsign (padded with spaces, as OpenSky sends it)
    "Israel",       # origin_country
    1735400000,     # time_position
    1735400005,     # last_contact
    34.777,         # longitude
    32.011,         # latitude
    10668.0,        # baro_altitude
    False,          # on_ground
    230.5,          # velocity
    88.2,           # true_track
    0.0,            # vertical_rate
    None,           # sensors
    10972.0,        # geo_altitude
    "5051",         # squawk
    False,          # spi
    0,              # position_source
    3,              # category
]

# Older/shorter arrays omit the trailing `category` field (17 elements).
SHORT_STATE = FULL_STATE[:17]


def test_from_array_maps_all_fields_by_index() -> None:
    state = StateVector.from_array(FULL_STATE)

    assert state.icao24 == "4ca7b3"
    assert state.callsign == "ELY001"  # whitespace stripped
    assert state.origin_country == "Israel"
    assert state.longitude == pytest.approx(34.777)
    assert state.latitude == pytest.approx(32.011)
    assert state.baro_altitude == pytest.approx(10668.0)
    assert state.on_ground is False
    assert state.velocity == pytest.approx(230.5)
    assert state.vertical_rate == pytest.approx(0.0)
    assert state.squawk == "5051"
    assert state.category == 3


def test_from_array_handles_missing_optional_category() -> None:
    state = StateVector.from_array(SHORT_STATE)
    assert state.category is None
    assert state.icao24 == "4ca7b3"


def test_from_array_handles_null_callsign_and_position() -> None:
    arr = list(FULL_STATE)
    arr[1] = None  # no callsign assigned
    arr[5] = None  # longitude unknown (e.g. no recent position)
    arr[6] = None  # latitude unknown

    state = StateVector.from_array(arr)

    assert state.callsign is None
    assert state.longitude is None
    assert state.latitude is None


def test_from_array_rejects_too_short_array() -> None:
    with pytest.raises(ValueError, match="too short"):
        StateVector.from_array(FULL_STATE[:5])
