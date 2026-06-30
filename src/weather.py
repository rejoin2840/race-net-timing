"""
weather.py — lightweight track-weather poll (Open-Meteo, no API key).

Rain invalidates the whole strategic model — pit costs, stint lengths, pace and
catch math all change, and auto-tuning pace knobs across a dry→wet transition
would learn garbage. So the dashboard polls this on a slow timer to (a) show
conditions in the header and (b) pause auto-tune while it's wet.

Stdlib only (urllib) so the app stays dependency-light. Every failure path
degrades to "unavailable" — never raises into the UI. Results are cached for
POLL_TTL_S so the slow UI timer can call freely.
"""

import json
import time
import urllib.request
from dataclasses import dataclass
from typing import Optional

API = "https://api.open-meteo.com/v1/forecast"
POLL_TTL_S = 300          # don't hit the API more than once per 5 min
WET_PRECIP_MM = 0.1       # precipitation at/above this (mm) → treat track as wet

# Open-Meteo WMO weather codes → short label (only the ones we care to name)
_WMO = {
    0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Violent showers",
    95: "Thunderstorm", 96: "Thunderstorm", 99: "Thunderstorm",
}


@dataclass
class Weather:
    ok:        bool
    is_wet:    bool = False
    temp_c:    Optional[float] = None
    precip_mm: Optional[float] = None
    wind_kmh:  Optional[float] = None
    condition: str = "—"

    def summary(self) -> str:
        if not self.ok:
            return "weather n/a"
        t = f"{self.temp_c:.0f}°C" if self.temp_c is not None else "—"
        tag = "WET" if self.is_wet else "dry"
        return f"{self.condition} · {t} · {tag}"


class WeatherPoll:
    def __init__(self, lat: float, lon: float):
        self.lat, self.lon = lat, lon
        self._cached: Optional[Weather] = None
        self._fetched_at: float = 0.0

    def get(self) -> Weather:
        now = time.time()
        if self._cached is not None and now - self._fetched_at < POLL_TTL_S:
            return self._cached
        self._cached = self._fetch()
        self._fetched_at = now
        return self._cached

    def _fetch(self) -> Weather:
        try:
            url = (f"{API}?latitude={self.lat}&longitude={self.lon}"
                   "&current=temperature_2m,precipitation,weather_code,wind_speed_10m")
            with urllib.request.urlopen(url, timeout=6) as resp:
                data = json.loads(resp.read().decode())
            cur = data.get("current", {})
            precip = cur.get("precipitation")
            code = cur.get("weather_code")
            return Weather(
                ok=True,
                is_wet=(precip is not None and precip >= WET_PRECIP_MM),
                temp_c=cur.get("temperature_2m"),
                precip_mm=precip,
                wind_kmh=cur.get("wind_speed_10m"),
                condition=_WMO.get(code, "—"),
            )
        except Exception:
            return Weather(ok=False)
