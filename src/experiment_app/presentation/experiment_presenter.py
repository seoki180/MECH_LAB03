from experiment_app.domain.session import ACTIVE
from .view_models import metric_model


class ExperimentPresenter:
    def __init__(self, sessions, telemetry, copier, clock, nmea_sources=None, nmea_path=None):
        self.sessions, self.telemetry, self.copier, self.clock = sessions, telemetry, copier, clock
        self.nmea_sources, self.nmea_path = nmea_sources, nmea_path
        self.visible = {"B": ("B.0", "B.1"), "C": ("C.0", "C.1")}

    def select_nmea(self, path):
        channels, sensors, gps = self.nmea_sources(path)
        self.sessions.set_sources(channels, sensors, gps)
        self.nmea_path = path

    def robot_status(self):
        robot = self.sessions.robot
        if robot is None:
            return "로봇 인터페이스 없음"
        status = robot.view()
        label = "데모 로봇" if status.demo else "로봇"
        session = self.sessions.view()
        if session and status.session_id != session.session_id:
            return f"{label} · 미연결 · 시작 시 설정 전송"
        return f"{label} · {status.state}" + (f" · {status.message}" if status.message else "")

    def metrics(self, snapshot):
        samples = {sample.channel_id: sample for sample in snapshot.samples}
        live = self.sessions.view().state in ACTIVE
        return {channel: metric_model(label, unit, samples.get(channel), self.clock.monotonic(), live)
                for part, channel, label, unit in self.sessions.channels if channel in self.visible.get(part, ())}

    def copy(self, part=None):
        visible = {channel for key, channels in self.visible.items()
                   if part is None or key == part for channel in channels}
        return self.copier.build_tsv(self.sessions.view(), self.telemetry.snapshot(), visible, include_gps=part is None)
