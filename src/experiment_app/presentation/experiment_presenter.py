from experiment_app.domain.session import ACTIVE
from experiment_app.domain.test_definition import AppError
from .view_models import metric_model


class ExperimentPresenter:
    def __init__(self, sessions, telemetry, copier, clock, nmea_sources=None, nmea_path=None,
                 analysis=None, lan_sources=None, settings_store=None, lan_defaults=None,
                 robot_sources=None, robot_defaults=None):
        self.sessions, self.telemetry, self.copier, self.clock = sessions, telemetry, copier, clock
        self.nmea_sources, self.nmea_path = nmea_sources, nmea_path
        self.analysis = analysis
        # 센서·GPS를 어디서 받는지. "nmea"는 파일 재생, "lan"은 HI-EDGE WebSocket이다.
        # 모드를 세션이 아니라 여기서 들고 있는 이유는, 화면이 현재 입력원을 사실대로
        # 표시해야 하고 세션은 무엇이 꽂혔는지 모르기 때문이다.
        self.lan_sources = lan_sources
        self.source_mode = "nmea"
        # 주소는 현장마다 다르다(169.254 자동 구성, 192.168.33 직결 등). 지난 실행에서
        # 쓰던 값을 불러와 매번 다시 입력하지 않게 한다. 기본값은 bootstrap이 준다
        # (presentation이 infrastructure를 직접 import하지 않는다).
        self.settings_store = settings_store
        defaults = dict(lan_defaults or {"host": "", "port": 8443, "verify": True, "certificate": None})
        self.lan_settings = settings_store.lan(defaults) if settings_store else defaults
        self.save_error = ""
        # 로봇은 센서와 다른 기기다(HI-EDGE와 주소·포트가 따로다). 같은 설정 화면에서
        # 다루지만 값은 섞지 않는다. robot_sources는 주소로 전송을 만드는 함수다.
        self.robot_sources = robot_sources
        robot_default = dict(robot_defaults or {"host": "", "port": 8080, "enabled": False})
        self.robot_settings = settings_store.robot(robot_default) if settings_store else robot_default
        self.robot_save_error = ""
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

        이미 LAN 모드일 때 다시 불러도 된다. 주소를 고쳐 적용하는 경로가 이것이다.
        """
        if self.lan_sources is None:
            raise AppError("VALIDATION_FAILED", "LAN 소스 구성이 없습니다.")
        settings = {"host": host, "port": port, "verify": verify, "certificate": certificate}
        channels, sensors, gps = self.lan_sources(**settings)
        # set_sources가 거부하면(실험 창이 열려 있음) 설정을 바꾸지 않는다. 화면에만
        # 새 주소가 남고 실제로는 옛 주소로 접속하는 상태를 만들지 않는다.
        self.sessions.set_sources(channels, sensors, gps)
        self.lan_settings = settings
        self.source_mode = "lan"
        self.remember_lan(settings)

    def remember_lan(self, settings):
        """다음 실행에서도 쓰도록 주소를 남긴다.

        저장 실패가 '적용'을 되돌리지는 않는다. 이번 실행에는 이미 반영됐기 때문이다.
        대신 사실대로 알리고(save_error) 화면이 그것을 보여준다.
        """
        self.save_error = ""
        if self.settings_store is None:
            return
        try:
            self.settings_store.save_lan(settings)
        except OSError as error:
            self.save_error = f"주소를 저장하지 못해 다음 실행에는 남지 않습니다: {error}"

    def use_nmea(self):
        """다시 파일 재생으로 돌린다. 선택된 파일이 없으면 거부한다."""
        if not self.nmea_path:
            raise AppError("VALIDATION_FAILED", "재생할 NMEA 파일을 먼저 선택하세요.")
        self.select_nmea(self.nmea_path)

    # --- 로봇 연결 ---

    def use_robot(self, host, port, enabled=True):
        """로봇 전송을 주소에 맞춰 바꾼다.

        센서 LAN과 같은 모양이지만 다른 기기다. enabled가 False면 데모 로봇으로
        되돌린다 — 주소만 남기고 실제로는 보내지 않는 상태다.

        주소를 켜면서 비워 두는 것은 거부한다. 빈 주소로 전송을 만들면 시작할 때
        비로소 실패하고, 사용자는 원인을 설정 화면에서 찾지 못한다.
        """
        if self.robot_sources is None:
            raise AppError("VALIDATION_FAILED", "로봇 전송 구성이 없습니다.")
        host = (host or "").strip()
        if enabled and not host:
            raise AppError("VALIDATION_FAILED", "로봇 주소를 입력하세요.")
        settings = {"host": host, "port": port, "enabled": bool(enabled)}
        # set_robot이 거부하면(실험 창이 열려 있음) 설정을 바꾸지 않는다. 화면에만
        # 새 주소가 남고 실제로는 옛 로봇으로 보내는 상태를 만들지 않는다.
        self.sessions.set_robot(self.robot_sources(**settings))
        self.robot_settings = settings
        self.remember_robot(settings)

    def remember_robot(self, settings):
        """다음 실행에서도 쓰도록 로봇 주소를 남긴다. 실패해도 적용은 되돌리지 않는다."""
        self.robot_save_error = ""
        if self.settings_store is None:
            return
        try:
            self.settings_store.save_robot(settings)
        except OSError as error:
            self.robot_save_error = f"로봇 주소를 저장하지 못해 다음 실행에는 남지 않습니다: {error}"

    def probe_robot(self, host, port):
        """로봇에 /health를 한 번 보내 받을 준비가 됐는지 확인한다.

        시험 흐름에는 /health가 없으므로(시작하면 바로 세팅값을 보낸다) 로봇이 꺼져
        있는지 미리 아는 수단이 이것뿐이다. (성공, 문구)를 돌려준다.

        소켓을 열므로 호출부가 작업 스레드에서 불러야 한다. 적용된 전송을 쓰지 않고
        입력된 주소로 새로 만든다 — 아직 적용하지 않은 주소도 시험할 수 있어야 한다.
        """
        if self.robot_sources is None:
            return False, "로봇 전송 구성이 없습니다."
        host = (host or "").strip()
        if not host:
            return False, "로봇 주소를 입력하세요."
        transport = self.robot_sources(host=host, port=port, enabled=True)
        if not hasattr(transport, "probe"):
            return False, "이 전송은 연결 시험을 제공하지 않습니다."
        return transport.probe(timeout=3)

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
