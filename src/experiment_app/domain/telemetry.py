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
class StreamGap:
    """수신이 끊기고 돌아온 사실. 기록에 남겨 빈 구간의 이유를 밝힌다.

    표본이 아니다(``channel_id`` 가 없다). 빈 구간을 그냥 두면 나중에 기록만 보고는
    자료가 왜 없는지 알 수 없고, 단절이 있었던 시험이 온전한 시험처럼 보인다.
    """

    session_id: str
    kind: str                      # "단절" 또는 "복구"
    received_time_utc: str
    monotonic_received: float
    elapsed: float
    duration_s: float | None = None
    detail: str = ""


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
