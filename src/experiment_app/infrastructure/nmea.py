"""Replay NovAtel INSPVAXA records supplied in .nmea files."""

from bisect import bisect_right
from dataclasses import dataclass
import math
from pathlib import Path

from experiment_app.domain.telemetry import GpsFix, SensorSample, displacement
from experiment_app.domain.test_definition import AppError


NMEA_CHANNELS = (
    ("A", "A.0", "속도 · NMEA", "km/h"),
    ("A", "A.1", "시작점 직선거리", "m"),
    ("B", "B.0", "동쪽 이동", "m"),
    ("B", "B.1", "북쪽 이동", "m"),
    ("C", "C.0", "현재 속도", "km/h"),
    ("C", "C.1", "누적 이동거리", "m"),
)


@dataclass(frozen=True)
class Position:
    elapsed: float
    latitude: float | None
    longitude: float | None
    altitude: float | None
    north_velocity: float | None
    east_velocity: float | None
    quality: str


def parse_inspvaxa(line: str) -> Position | None:
    """Return one position. Other sentence types are skipped."""
    if not line.startswith("#INSPVAXA,"):
        return None
    try:
        header, body = line.strip().split(";", 1)
        head = header.split(",")
        fields = body.split("*", 1)[0].split(",")
        elapsed = float(head[6]) + int(head[5]) * 604800
        latitude, longitude, altitude = (float(fields[i]) for i in (2, 3, 4))
        north, east = (float(fields[i]) for i in (6, 7))
        if not all(math.isfinite(v) for v in (elapsed, latitude, longitude, altitude, north, east)):
            raise ValueError("non-finite value")
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError("coordinates out of range")
        quality = fields[1]
        valid = fields[0] in {"INS_ALIGNMENT_COMPLETE", "INS_SOLUTION_GOOD"} and quality != "NONE"
        return Position(elapsed, latitude if valid else None, longitude if valid else None,
                        altitude if valid else None, north if valid else None, east if valid else None,
                        quality if valid else "fix 없음")
    except (IndexError, ValueError) as error:
        raise ValueError("invalid INSPVAXA record") from error


class NmeaReplay:
    def __init__(self, path, clock):
        self.path, self.clock = Path(path), clock
        self.positions = ()
        self.times = ()
        self.start_position = None
        self.duration = None
        self.travelled = ()

    def prepare(self):
        positions = []
        try:
            with self.path.open(encoding="ascii", errors="replace") as source:
                for number, line in enumerate(source, 1):
                    try:
                        point = parse_inspvaxa(line)
                    except ValueError as error:
                        raise AppError("NMEA_INVALID", f"{self.path.name} {number}행: {error}") from error
                    if point is not None and (not positions or point.elapsed > positions[-1].elapsed):
                        positions.append(point)
        except OSError as error:
            raise AppError("NMEA_UNAVAILABLE", f"NMEA 파일을 읽을 수 없습니다: {self.path}") from error
        start = next((p for p in positions if p.latitude is not None), None)
        if start is None:
            raise AppError("NMEA_NO_FIX", "파일에 유효한 INSPVAXA 위치가 없습니다.")
        self.positions = tuple(positions)
        self.times = tuple(p.elapsed - positions[0].elapsed for p in positions)
        self.start_position = start
        self.duration = self.times[-1]
        self.travelled = self._travelled(self.positions)

    @staticmethod
    def _travelled(positions):
        """각 위치까지의 누적 경로 이동거리(m). 시작점 직선거리와 달리 궤적을 따라 더한다.

        fix가 없는 구간은 거리를 알 수 없으므로 더하지 않고 직전 누적값을 유지한다.
        """
        totals, total, previous = [], 0.0, None
        for point in positions:
            if point.latitude is not None:
                if previous is not None:
                    total += displacement(previous.latitude, previous.longitude,
                                          point.latitude, point.longitude)[2]
                previous = point
            totals.append(total)
        return tuple(totals)

    def _index(self, elapsed):
        if not self.positions:
            raise AppError("NMEA_NOT_READY", "NMEA 재생이 준비되지 않았습니다.")
        return max(0, bisect_right(self.times, elapsed) - 1)

    def at(self, elapsed):
        return self.positions[self._index(elapsed)]

    def sensor_samples(self, session_id, elapsed, sequence, scenario):
        index = self._index(elapsed)
        point = self.positions[index]
        start = self.start_position
        values = (None,) * 6
        if point.latitude is not None:
            east, north, distance = displacement(start.latitude, start.longitude, point.latitude, point.longitude)
            speed = math.hypot(point.north_velocity, point.east_velocity) * 3.6
            values = (speed, distance, east, north, speed, self.travelled[index])
        timestamp, mono = self.clock.now_utc(), self.clock.monotonic()
        return tuple(SensorSample(session_id, channel, sequence, timestamp, mono, value, unit,
                                  f"NMEA · {point.quality}")
                     for (_, channel, _, unit), value in zip(NMEA_CHANNELS, values))

    def gps_fix(self, session_id, elapsed, scenario):
        point = self.at(elapsed)
        if scenario == "GPS fix 손실" and elapsed > 3:
            point = Position(point.elapsed, None, None, None, None, None, "fix 없음")
        return GpsFix(session_id, point.latitude, point.longitude, self.clock.now_utc(),
                      self.clock.monotonic(), f"NMEA · {point.quality}", point.altitude)


class NmeaSensorSource:
    def __init__(self, replay):
        self.replay = replay

    def prepare(self):
        self.replay.prepare()
        self.duration = self.replay.duration

    def read(self, session_id, elapsed, sequence, scenario):
        return self.replay.sensor_samples(session_id, elapsed, sequence, scenario)


class NmeaGpsSource:
    def __init__(self, replay):
        self.replay = replay

    def read(self, session_id, elapsed, scenario):
        return self.replay.gps_fix(session_id, elapsed, scenario)
