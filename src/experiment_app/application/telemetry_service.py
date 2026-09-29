from collections import deque
from dataclasses import replace
from threading import RLock
from experiment_app.domain.telemetry import TelemetrySnapshot, effective_quality, displacement


class TelemetryService:
    def __init__(self):
        self.lock = RLock()
        self.reset("")

    def reset(self, session_id):
        with self.lock:
            self.session_id = session_id
            self.samples = {}
            self.gps = None
            self.last_valid_gps = None
            self.start_gps = None
            self.track = deque(maxlen=600)

    def ingest(self, samples, gps=None):
        with self.lock:
            for sample in samples:
                if sample.session_id != self.session_id:
                    continue
                previous = self.samples.get(sample.channel_id)
                if previous is None or sample.sequence > previous.sequence:
                    self.samples[sample.channel_id] = sample
            if gps and gps.session_id == self.session_id and (self.gps is None or gps.monotonic_received > self.gps.monotonic_received):
                self.gps = gps
                if gps.latitude is not None and gps.longitude is not None:
                    if self.start_gps is None:
                        self.start_gps = gps
                    self.last_valid_gps = gps
                    self.track.append((gps.latitude, gps.longitude))

    def snapshot(self):
        with self.lock:
            movement = None
            if self.start_gps and self.last_valid_gps:
                start, latest = self.start_gps, self.last_valid_gps
                movement = displacement(start.latitude, start.longitude, latest.latitude, latest.longitude)
            return TelemetrySnapshot(self.session_id, tuple(self.samples.values()), self.gps, tuple(self.track),
                                     self.last_valid_gps, self.start_gps, movement)

    def freeze_quality(self, now):
        with self.lock:
            self.samples = {key: replace(sample, quality=effective_quality(sample, now))
                            for key, sample in self.samples.items()}
