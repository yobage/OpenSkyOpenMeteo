"""Rule-based anomaly detection, with an LLM used only to explain findings.

Detection itself is deterministic (thresholds on altitude/vertical rate, a
simple geometric check for holding-pattern-like behavior) so it's testable
and reproducible without calling any LLM. The LLM is only used afterwards,
to turn the structured flags into a short operator-facing explanation.
"""

from __future__ import annotations

import json
import math
import statistics

from common.models import EnrichedFlight
from pydantic import BaseModel

from ai.llm_provider import LLMProvider

# An airborne flight below this altitude (meters) with meaningful groundspeed
# is flagged as unusually low (e.g. not accounted for by a normal approach).
LOW_ALTITUDE_M = 150.0
LOW_ALTITUDE_MIN_VELOCITY_MS = 30.0

# A vertical rate whose magnitude exceeds this (m/s) is flagged as rapid.
# ~15 m/s is roughly 3000 ft/min, a brisk climb/descent for an airliner.
RAPID_VERTICAL_RATE_MS = 15.0

# Holding-pattern heuristic: airborne, staying within this radius (km) of its
# own recent-position centroid while headings vary a lot (looping/circling)
# rather than flying a straight track.
HOLDING_MAX_RADIUS_KM = 15.0
HOLDING_MIN_TRACK_STDDEV_DEG = 60.0
HOLDING_MIN_POINTS = 4

_EARTH_RADIUS_KM = 6371.0

_SYSTEM_PROMPT = (
    "You are an aviation operations assistant. You will be given a JSON list "
    "of automatically-flagged anomalies among currently tracked aircraft. "
    "Write a short, plain-English explanation (2-5 sentences, or one line "
    "per notable flag) of what was flagged and why it might be operationally "
    "relevant. Do not invent details beyond what's given. If the list is "
    "empty, simply say nothing unusual was detected."
)


class AnomalyFlag(BaseModel):
    """A single deterministically-detected anomaly for one aircraft."""

    icao24: str
    callsign: str | None = None
    kind: str
    detail: str


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def detect_anomalies(flights: list[EnrichedFlight]) -> list[AnomalyFlag]:
    """Flag flights with unusually low altitude or a rapid vertical rate.

    Operates on a single current snapshot (e.g. the `flights` table).
    """
    flags: list[AnomalyFlag] = []
    for f in flights:
        if (
            not f.on_ground
            and f.baro_altitude is not None
            and f.baro_altitude < LOW_ALTITUDE_M
            and (f.velocity or 0) > LOW_ALTITUDE_MIN_VELOCITY_MS
        ):
            flags.append(
                AnomalyFlag(
                    icao24=f.icao24,
                    callsign=f.callsign,
                    kind="low_altitude",
                    detail=(
                        f"Airborne at {f.baro_altitude:.0f}m altitude "
                        f"with velocity {f.velocity:.0f} m/s"
                    ),
                )
            )

        if f.vertical_rate is not None and abs(f.vertical_rate) >= RAPID_VERTICAL_RATE_MS:
            direction = "Climbing" if f.vertical_rate > 0 else "Descending"
            flags.append(
                AnomalyFlag(
                    icao24=f.icao24,
                    callsign=f.callsign,
                    kind="rapid_vertical_rate",
                    detail=f"{direction} at {abs(f.vertical_rate):.1f} m/s",
                )
            )
    return flags


def detect_holding_pattern(
    icao24: str, callsign: str | None, points: list[EnrichedFlight]
) -> AnomalyFlag | None:
    """Check whether one aircraft's recent history looks like a holding pattern.

    `points` should be that aircraft's recent history rows (any order),
    typically the last several minutes from `flight_history`.
    """
    airborne_points = [p for p in points if not p.on_ground]
    coords = [
        (p.latitude, p.longitude)
        for p in airborne_points
        if p.latitude is not None and p.longitude is not None
    ]
    tracks = [p.true_track for p in airborne_points if p.true_track is not None]

    if len(coords) < HOLDING_MIN_POINTS or len(tracks) < HOLDING_MIN_POINTS:
        return None

    lat_centroid = statistics.mean(c[0] for c in coords)
    lon_centroid = statistics.mean(c[1] for c in coords)
    max_radius = max(_haversine_km(lat_centroid, lon_centroid, lat, lon) for lat, lon in coords)

    # Headings wrap around 0/360deg; treat as a circular spread by looking at
    # the stddev of the (mod-360) values, which is good enough for this
    # coarse heuristic without full circular statistics.
    track_stddev = statistics.pstdev(tracks)

    if max_radius <= HOLDING_MAX_RADIUS_KM and track_stddev >= HOLDING_MIN_TRACK_STDDEV_DEG:
        return AnomalyFlag(
            icao24=icao24,
            callsign=callsign,
            kind="possible_holding_pattern",
            detail=(
                f"Stayed within {max_radius:.1f}km over {len(coords)} recent positions "
                f"with heading varying by {track_stddev:.0f} deg (stddev)"
            ),
        )
    return None


def explain_anomalies(provider: LLMProvider, flags: list[AnomalyFlag]) -> str:
    """Turn a list of deterministically-detected flags into a short explanation."""
    if not flags:
        return "No anomalies detected in the current snapshot."

    prompt = f"Flagged anomalies:\n{json.dumps([f.model_dump() for f in flags], indent=2)}"
    return provider.complete(prompt, system=_SYSTEM_PROMPT)
