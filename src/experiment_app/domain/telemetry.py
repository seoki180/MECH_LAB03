from dataclasses import dataclass
import math


@dataclass(frozen=True)
class SensorSample:
    session_id: str
    channel_id: str
    sequence: int
    received_time_utc: str
    monotonic_received: float
    value: float | None
    unit: str
    quality: str = "정상"
    source_time_utc: str | None = None


@dataclass(frozen=True)
class GpsFix:
    session_id: str
    latitude: float | None
    longitude: float | None
    received_time_utc: str
    monotonic_received: float
    fix_quality: str = "정상"
    altitude: float | None = None


@dataclass(frozen=True)
class TelemetrySnapshot:
    session_id: str
    samples: tuple[SensorSample, ...]
    gps: GpsFix | None
    track: tuple[tuple[float, float], ...]
    last_valid_gps: GpsFix | None = None
    start_gps: GpsFix | None = None
    movement: tuple[float, float, float] | None = None  # east, north, direct distance (m)


def displacement(start_lat, start_lon, lat, lon):
    """WGS84 coordinate offsets near the start and great-circle distance in metres."""
    lat1, lon1 = math.radians(start_lat), math.radians(start_lon)
    lat2, lon2 = math.radians(lat), math.radians(lon)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    radius = 6371008.8
    north = radius * dlat
    east = radius * math.cos((lat1 + lat2) / 2) * dlon
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    distance = 2 * radius * math.atan2(math.sqrt(a), math.sqrt(max(0, 1 - a)))
    return east, north, distance


def effective_quality(sample, now, live=True):
    age = max(0, now - sample.monotonic_received)
    if live and age >= 5:
        return "연결 끊김"
    if live and age >= 2:
        return "지연"
    return sample.quality
