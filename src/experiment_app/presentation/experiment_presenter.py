from experiment_app.domain.session import ACTIVE
from experiment_app.domain.test_definition import AppError
from .view_models import metric_model


class ExperimentPresenter:
    def __init__(self, sessions, telemetry, copier, clock, nmea_sources=None, nmea_path=None,
                 analysis=None, lan_sources=None):
        self.sessions, self.telemetry, self.copier, self.clock = sessions, telemetry, copier, clock
        self.nmea_sources, self.nmea_path = nmea_sources, nmea_path
        self.analysis = analysis
        # 센서·GPS를 어디서 받는지. "nmea"는 파일 재생, "lan"은 HI-EDGE WebSocket이다.
        # 모드를 세션이 아니라 여기서 들고 있는 이유는, 화면이 현재 입력원을 사실대로
        # 표시해야 하고 세션은 무엇이 꽂혔는지 모르기 때문이다.
        self.lan_sources = lan_sources
        self.source_mode = "nmea"
        self.lan_settings = {}
        self.visible = {"B": ("B.0", "B.1"), "C": ("C.0", "C.1")}

    def analyse(self, session_id):
        """결과 그래프 값을 만든다. 기록을 읽으므로 GUI 스레드에서 오래 걸릴 수 있어
        호출자가 busy 표시를 해야 한다. 그래프 대상은 C 영역 첫 채널(현재 속도)이다.
        """
        if self.analysis is None:
            raise AppError("VALIDATION_FAILED", "결과 분석 서비스가 없습니다.")
        return self.analysis.analyse_session(session_id)

    def select_nmea(self, path):
        channels, sensors, gps = self.nmea_sources(path)
        self.sessions.set_sources(channels, sensors, gps)
        self.nmea_path = path
        self.source_mode = "nmea"

    def use_lan(self, host, port, verify=True, certificate=None):
        """센서·GPS를 HI-EDGE LAN 스트림으로 바꾼다.

        소켓은 여기서 열지 않는다. 세션 시작 시 ``prepare()`` 가 연결하므로, 설정만
        바꿔 두고 연결 성패는 시작 시점이나 '연결 시험'에서 확인한다.
        """
        if self.lan_sources is None:
            raise AppError("VALIDATION_FAILED", "LAN 소스 구성이 없습니다.")
        settings = {"host": host, "port": port, "verify": verify, "certificate": certificate}
        channels, sensors, gps = self.lan_sources(**settings)
        self.sessions.set_sources(channels, sensors, gps)
        self.lan_settings = settings
        self.source_mode = "lan"

    def use_nmea(self):
        """다시 파일 재생으로 돌린다. 선택된 파일이 없으면 거부한다."""
        if not self.nmea_path:
            raise AppError("VALIDATION_FAILED", "재생할 NMEA 파일을 먼저 선택하세요.")
        self.select_nmea(self.nmea_path)

    def source_status(self):
        """현재 입력원을 사실대로 한 줄로. 설정·실험 헤더가 함께 쓴다."""
        if self.source_mode != "lan":
            return "NMEA 파일 재생"
        sensors = self.sessions.sensors
        host = self.lan_settings.get("host", "")
        port = self.lan_settings.get("port", "")
        detail = getattr(sensors, "status", "연결 전")
        error = getattr(sensors, "last_error", "")
        reconnects = getattr(sensors, "reconnects", 0)
        line = f"LAN 실시간 · {host}:{port} · {detail}"
        if reconnects:
            line += f" · 재접속 {reconnects}회"
        if error and detail != "연결됨":
            line += f" · {error}"
        return line

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
