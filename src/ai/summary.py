"""Situational summary generation.

Stats are computed deterministically in Python first (counts, altitude
bands, weather averages), and only that compact summary — not raw rows —
is handed to the LLM. This keeps the prompt small and stops the model from
inventing numbers it wasn't given.
"""

from __future__ import annotations

import json
import statistics

from common.models import EnrichedFlight

from ai.llm_provider import LLMProvider

# An airborne flight below this altitude (meters) is treated as being on
# approach/departure for the purposes of the summary.
LOW_ALTITUDE_M = 1000.0

_SYSTEM_PROMPT = (
    "You are an aviation operations assistant producing a short situational "
    "summary for a live air-traffic dashboard covering the airspace near "
    "Israel. You will be given pre-computed statistics as JSON. Write 2-4 "
    "concise sentences in plain English. Use only the numbers provided — "
    "never invent or estimate a figure that isn't in the JSON. No markdown, "
    "no headers, just prose."
)


def _compute_stats(flights: list[EnrichedFlight]) -> dict:
    airborne = [f for f in flights if not f.on_ground]
    on_ground = [f for f in flights if f.on_ground]
    low_altitude = [
        f for f in airborne if f.baro_altitude is not None and f.baro_altitude < LOW_ALTITUDE_M
    ]
    velocities = [f.velocity for f in airborne if f.velocity is not None]
    temperatures = [
        f.weather_temperature_c for f in flights if f.weather_temperature_c is not None
    ]
    wind_speeds = [
        f.weather_wind_speed_kmh for f in flights if f.weather_wind_speed_kmh is not None
    ]

    return {
        "total_tracked": len(flights),
        "airborne": len(airborne),
        "on_ground": len(on_ground),
        "low_altitude_airborne_count": len(low_altitude),
        "low_altitude_threshold_m": LOW_ALTITUDE_M,
        "avg_velocity_ms": round(statistics.mean(velocities), 1) if velocities else None,
        "avg_temperature_c": round(statistics.mean(temperatures), 1) if temperatures else None,
        "avg_wind_speed_kmh": round(statistics.mean(wind_speeds), 1) if wind_speeds else None,
        "max_wind_speed_kmh": round(max(wind_speeds), 1) if wind_speeds else None,
    }


def generate_situational_summary(provider: LLMProvider, flights: list[EnrichedFlight]) -> str:
    """Produce a natural-language situational summary of current air traffic."""
    if not flights:
        return "No aircraft are currently tracked in the monitored airspace."

    stats = _compute_stats(flights)
    prompt = f"Current statistics:\n{json.dumps(stats, indent=2)}"
    return provider.complete(prompt, system=_SYSTEM_PROMPT)
