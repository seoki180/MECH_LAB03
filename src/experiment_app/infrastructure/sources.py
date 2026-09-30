from datetime import datetime, timezone
import math
import time
from experiment_app.domain.telemetry import SensorSample, GpsFix


class SystemClock:
    def now_utc(self):
        return datetime.now(timezone.utc).isoformat()

    def monotonic(self):
        return time.monotonic()


class FakeSensorSource:
    """가상 센서. 끝이 없으므로 중지할 때까지 계속 값을 만든다."""

    continuous = True

    def __init__(self, channels, clock):
        self.channels, self.clock = channels, clock

    def read(self, session_id, elapsed, sequence, scenario):
        samples = []
        for index, (part, channel_id, label, unit) in enumerate(self.channels):
            if scenario == "센서 지연/단절" and part == "A" and elapsed >= 2:
                continue
            value = 25 + index * 12 + math.sin(elapsed * 0.8 + index) * 8
            invalid = scenario == "값 오류" and part == "B" and elapsed > 2
            samples.append(SensorSample(session_id, channel_id, sequence, self.clock.now_utc(),
                           self.clock.monotonic(), None if invalid else value, unit,
                           "값 오류" if invalid else "정상"))
        return tuple(samples)


class FakeGpsSource:
    def __init__(self, clock):
        self.clock = clock

    def read(self, session_id, elapsed, scenario):
        valid = not (scenario == "GPS fix 손실" and elapsed > 3)
        return GpsFix(session_id, 37.4 + math.sin(elapsed / 12) * 0.001 if valid else None,
                      127.1 + math.cos(elapsed / 12) * 0.001 if valid else None,
                      self.clock.now_utc(), self.clock.monotonic(), "정상" if valid else "fix 없음")
