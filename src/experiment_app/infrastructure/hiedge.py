"""HI-EDGE LAN 속도 스트림(WebSocket) 수신 소스.

장비는 ``wss://<host>:8443/ws/data`` 로 접속하면 먼저 ``hello`` 를, 이어서 ``speed``
메시지를 명목 50 Hz로 계속 보낸다. 구독 요청은 보내지 않고 받기만 한다.

구조상의 자리
-------------
``StreamingSensorSource`` 를 상속한 push 소스다. 수신 스레드가 도착한 원본 메시지를
큐에 넣고, 세션 작업 스레드가 ``read()`` 로 꺼내 ``SensorSample`` 로 바꾼다. 변환을
``read()`` 쪽에서 하는 이유는 두 가지다.

- 표본에 박을 ``session_id`` 를 수신 스레드가 모른다. ``read()`` 는 안다.
- 기준점(시작 좌표)은 **세션마다** 새로 잡아야 한다. ``read()`` 에서 session_id가
  바뀌는 것을 보고 기준점·누적거리를 초기화한다.

규격이 아직 바뀌는 중이다
-------------------------
확인된 ``speed`` 메시지의 최상위 필드는 다음과 같다(실측, 2026-10-02)::

    schema, type, stream_id, seq, publication_hz, output_time_utc,
    output_monotonic_s, source_interval_s, source_gap, valid,
    input_connected, input_age_s, estimate

``estimate`` 는 유효할 때 ``speed_kmh`` / ``speed_mps`` / ``measurement_kind`` 를
담는다(무효일 때 ``null``).

위도·경도는 **아직 들어오지 않는다.** 추가 예정이라고 전달받았으므로 이 모듈은
좌표를 여러 후보 이름으로 찾아보고, 없으면 좌표 기반 채널을 ``None`` 으로 둔다.
**지어내지 않는다.** 속도를 적분해 거리처럼 보이게 만들지도 않는다 — 경로 적분과
다른 값이고, 사용자가 둘을 구별할 수 없다.
"""

import json
import math
import ssl
import threading
import time

from experiment_app.domain.telemetry import GpsFix, SensorSample, displacement
from experiment_app.domain.test_definition import AppError
from experiment_app.infrastructure.streaming import StreamingSensorSource

SCHEMA = "hi-edge.speed-stream.v1"
DEFAULT_HOST = "169.254.32.88"
DEFAULT_PORT = 8443
PATH = "/ws/data"

# 장비 공개 인증서의 기본 파일 이름. 실행 파일 옆(external_root)에서 찾는다.
CERT_NAME = "hi-edge-ui-cert.pem"

# NMEA 재생과 같은 6채널을 쓴다. 라벨만 소스를 밝힌다. 채널 id가 같아야 결과 그래프와
# 복사 대상 선택(visible)이 두 모드에서 같게 동작한다.
LAN_CHANNELS = (
    ("A", "A.0", "속도 · LAN", "km/h"),
    ("A", "A.1", "시작점 직선거리", "m"),
    ("B", "B.0", "횡방향", "m"),
    ("B", "B.1", "종방향", "m"),
    ("C", "C.0", "현재 속도", "km/h"),
    ("C", "C.1", "누적 이동거리", "m"),
)

# 좌표가 이 속도 미만이면 정지로 보고 누적거리에 더하지 않는다. NMEA 재생과 같은
# 판정값을 쓴다(infrastructure/nmea.py). 실제 장비의 정지 판정 기준은 확인이 필요하다.
MIN_MOVING_SPEED_MPS = 0.3

# 좌표 필드 이름 후보. 규격이 확정되면 하나로 줄인다.
LAT_KEYS = ("latitude", "lat", "lat_deg", "latitude_deg")
LON_KEYS = ("longitude", "lon", "lng", "lon_deg", "longitude_deg")


def _number(source, keys):
    """후보 이름 중 먼저 찾은 유한한 수를 돌려준다. 없으면 None."""
    if not isinstance(source, dict):
        return None
    for key in keys:
        value = source.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return float(value)
    return None


def _coordinates(message):
    """메시지에서 위도·경도를 찾는다. 최상위와 estimate/position 안을 함께 본다."""
    for scope in (message.get("estimate"), message.get("position"), message):
        latitude, longitude = _number(scope, LAT_KEYS), _number(scope, LON_KEYS)
        if latitude is None or longitude is None:
            continue
        if -90 <= latitude <= 90 and -180 <= longitude <= 180:
            return latitude, longitude
    return None, None


def _speed_kmh(message):
    """km/h 속도. valid가 아니거나 값이 없으면 None."""
    if message.get("valid") is not True:
        return None
    estimate = message.get("estimate")
    kmh = _number(estimate, ("speed_kmh",))
    if kmh is not None:
        return kmh
    mps = _number(estimate, ("speed_mps",))
    return mps * 3.6 if mps is not None else None


def _quality(message, speed):
    """표시·기록에 남길 품질 문구. 사실만 적는다."""
    if message.get("input_connected") is False:
        return "LAN · 입력 끊김"
    if speed is None:
        return "LAN · 속도 사용 불가"
    kind = message.get("estimate", {}).get("measurement_kind") if isinstance(message.get("estimate"), dict) else None
    label = f"LAN · {kind}" if kind else "LAN · 정상"
    if message.get("source_gap") is True:
        return f"{label} · 자료 단절"
    return label


def certificate_path():
    """장비 공개 인증서의 기본 위치. 실행 파일 밖에 두어 교체할 수 있게 한다."""
    from experiment_app import paths
    return paths.external_root() / CERT_NAME


def tls_context(certificate=None, verify=True):
    """TLS 설정. 검증을 끄는 것은 호출부가 명시적으로 선택해야 한다.

    인증서가 없을 때 조용히 검증을 끄지 않는다. 그러면 접속 대상이 장비인지
    확인하지 못한 채 '연결됨'으로 보이게 된다.
    """
    if not verify:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    path = certificate or certificate_path()
    if not path.is_file():
        raise AppError("LAN_CERT_MISSING",
                       f"장비 공개 인증서를 찾을 수 없습니다: {path}\n"
                       f"{CERT_NAME} 파일을 그 위치에 두거나, 시험용으로 인증서 검증 생략을 선택하세요.")
    try:
        return ssl.create_default_context(cafile=str(path))
    except (ssl.SSLError, OSError) as error:
        raise AppError("LAN_CERT_INVALID", f"인증서를 읽을 수 없습니다: {path} ({error})") from error


def stream_url(host=DEFAULT_HOST, port=DEFAULT_PORT):
    return f"wss://{host}:{port}{PATH}"


def probe(host=DEFAULT_HOST, port=DEFAULT_PORT, certificate=None, verify=True, timeout=6.0):
    """연결과 수신을 한 번 확인한다. 설정 화면의 '연결 시험'이 쓴다.

    GUI 스레드에서 부르면 timeout 동안 멈추므로 호출부가 작업 스레드로 돌린다.
    성공하면 사람이 읽을 요약 문구를, 실패하면 AppError를 낸다.
    """
    try:
        from websockets.sync.client import connect
    except ImportError as error:
        raise AppError("LAN_LIB_MISSING", "websockets 패키지가 없습니다. websockets==15.0.1을 설치하세요.") from error
    tls = tls_context(certificate, verify)
    url = stream_url(host, port)
    try:
        with connect(url, ssl=tls, proxy=None, compression=None,
                     open_timeout=timeout, close_timeout=1, max_size=65536) as ws:
            hello = json.loads(ws.recv(timeout=timeout))
            if hello.get("type") != "hello" or hello.get("schema") != SCHEMA:
                raise AppError("LAN_SCHEMA_MISMATCH",
                               f"예상한 HI-EDGE 속도 스트림이 아닙니다: {hello.get('schema')} / {hello.get('type')}")
            message = json.loads(ws.recv(timeout=timeout))
            speed = _speed_kmh(message)
            latitude, _ = _coordinates(message)
            lines = [f"연결됨 · {url}",
                     f"stream_id {hello.get('stream_id')} · 출력 {hello.get('publication_hz')} Hz",
                     f"첫 표본 seq {message.get('seq')} · {_quality(message, speed)}",
                     "속도 " + ("사용 불가(valid=false)" if speed is None else f"{speed:.3f} km/h"),
                     "좌표 " + ("수신됨" if latitude is not None else "아직 없음 · 거리/방향/지도는 표시하지 않습니다")]
            return "\n".join(lines)
    except AppError:
        raise
    except ssl.SSLCertVerificationError as error:
        raise AppError("LAN_CERT_INVALID",
                       f"인증서 확인 실패: 장비의 공개 인증서와 접속 주소를 확인하세요. ({error})") from error
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise AppError("LAN_SCHEMA_MISMATCH", f"메시지 형식 확인 필요: {error}") from error
    except TimeoutError as error:
        raise AppError("LAN_TIMEOUT", f"응답이 없습니다: {url} (이더넷 연결과 PC IP 설정을 확인하세요)") from error
    except Exception as error:
        # websockets의 예외 계층을 여기서 열거하지 않는다. 종류 이름을 그대로 보여준다.
        raise AppError("LAN_UNAVAILABLE", f"{url} 연결 실패 · {type(error).__name__}: {error}") from error


class HiEdgeSpeedSource(StreamingSensorSource):
    """HI-EDGE LAN 스트림을 받는 센서 소스.

    끝이 없으므로 ``continuous = True``. 사용자가 중지할 때까지 받는다.
    """

    continuous = True

    def __init__(self, clock, host=DEFAULT_HOST, port=DEFAULT_PORT,
                 certificate=None, verify=True, capacity=20000):
        # 50 Hz × 20000 ≒ 400초. 소비가 밀려도 버린 사실을 세어 남긴다.
        super().__init__(capacity=capacity)
        self.clock = clock
        self.host, self.port = host, port
        self.certificate, self.verify = certificate, verify
        self.url = stream_url(host, port)
        self._thread = None
        self._stop = threading.Event()
        # 수신 스레드와 세션 스레드가 함께 읽는 상태. _state_lock으로만 바꾼다.
        self._state_lock = threading.Lock()
        self.status = "연결 전"
        self.last_error = ""
        self.reconnects = 0
        self.received = 0
        # read()가 관리하는 세션별 상태. 세션 스레드만 건드린다.
        self._session_id = None
        self._origin = None
        self._previous = None
        self._travelled = 0.0
        self._latest_fix = None

    # --- 연결 수명 ---

    def connect(self):
        """수신 스레드를 띄운다. 첫 연결이 실패하면 시작을 막는다.

        여기서 조용히 넘기면 '시작했는데 값이 안 온다'로만 보인다. 주소·인증서·IP
        설정 문제는 시작 시점에 알려 주는 쪽이 낫다. 연결된 뒤의 단절은 스레드가
        재접속으로 처리한다.
        """
        try:
            from websockets.sync.client import connect as ws_connect
        except ImportError as error:
            raise AppError("LAN_LIB_MISSING",
                           "websockets 패키지가 없습니다. websockets==15.0.1을 설치하세요.") from error
        tls = tls_context(self.certificate, self.verify)
        self._stop.clear()
        self._thread = threading.Thread(target=self._receive_loop, args=(ws_connect, tls),
                                        name="mechlab-hiedge", daemon=True)
        self._thread.start()
        # 첫 hello를 받을 때까지 기다린다. 못 받으면 시작을 거부한다.
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            with self._state_lock:
                if self.received:
                    return
                error = self.last_error
            if error:
                self._stop.set()
                raise AppError("LAN_UNAVAILABLE", f"{self.url} 연결 실패 · {error}")
            time.sleep(0.05)
        self._stop.set()
        raise AppError("LAN_TIMEOUT", f"{self.url} 에서 자료를 받지 못했습니다. 이더넷 연결과 PC IP 설정을 확인하세요.")

    def disconnect(self):
        self._stop.set()
        thread = self._thread
        if thread is not None:
            # GUI 스레드가 아니라 세션 작업 스레드에서 호출된다. 무기한 기다리지 않는다.
            thread.join(timeout=3)
            if thread.is_alive():
                raise AppError("LAN_CLEANUP_FAILED", "LAN 수신 스레드가 3초 안에 종료되지 않았습니다.")
        self._thread = None
        with self._state_lock:
            self.status = "연결 해제"

    def _set_status(self, status, error=""):
        with self._state_lock:
            self.status = status
            if error:
                self.last_error = error

    def _receive_loop(self, ws_connect, tls):
        """끊기면 1초 뒤 재접속한다. 단절 구간의 메시지는 다시 오지 않는다."""
        # ws:// 는 TLS를 쓰지 않는다. 실장비는 wss:// 이고, 평문은 시험용 지역 서버뿐이다.
        secure = self.url.startswith("wss://")
        while not self._stop.is_set():
            try:
                with ws_connect(self.url, ssl=tls if secure else None, proxy=None, compression=None,
                                open_timeout=5, close_timeout=1, max_size=65536) as ws:
                    hello = json.loads(ws.recv(timeout=5))
                    if hello.get("schema") != SCHEMA or hello.get("type") != "hello":
                        self._set_status("규격 불일치", f"schema={hello.get('schema')}")
                        return
                    stream_id = hello.get("stream_id")
                    expected = hello.get("next_seq")
                    self._set_status(f"연결됨 · {hello.get('publication_hz')} Hz")
                    while not self._stop.is_set():
                        message = json.loads(ws.recv(timeout=2))
                        if message.get("type") != "speed":
                            continue
                        # 순번·스트림이 끊기면 그 사실을 표본에 남긴다. 조용히 이어 붙이면
                        # 없던 구간이 연속 자료처럼 보인다.
                        broken = message.get("stream_id") != stream_id or message.get("seq") != expected
                        stream_id, expected = message.get("stream_id"), (message.get("seq") or 0) + 1
                        if broken:
                            message = {**message, "source_gap": True}
                        with self._state_lock:
                            self.received += 1
                        self.submit(message)
            except Exception as error:
                if self._stop.is_set():
                    return
                with self._state_lock:
                    self.reconnects += 1
                self._set_status("재접속 대기", f"{type(error).__name__}: {error}")
                # 이전 속도를 현재 값으로 쓰면 안 된다. 큐에 아무것도 넣지 않으므로
                # 화면은 '수신 대기'로, 5초가 지나면 세션 watchdog이 단절로 끝낸다.
                self._stop.wait(1)

    # --- 세션 스레드가 꺼내 가는 부분 ---

    def _reset_session(self, session_id):
        """세션이 바뀌면 기준점을 다시 잡는다.

        기준점은 **시작을 누른 뒤 처음 받은 유효 좌표**다. 그 전에는 거리·방향을
        None으로 두고 '기준점 대기'로 표시한다. 0으로 채우지 않는다.
        """
        self._session_id = session_id
        self._origin = None
        self._previous = None
        self._travelled = 0.0

    def read(self, session_id, elapsed, sequence, scenario):
        if session_id != self._session_id:
            self._reset_session(session_id)
        samples = []
        for message in self._drain():
            samples.extend(self._to_samples(session_id, message))
        return tuple(samples)

    def _to_samples(self, session_id, message):
        speed = _speed_kmh(message)
        quality = _quality(message, speed)
        latitude, longitude = _coordinates(message)
        distance = east = north = travelled = None
        if latitude is not None:
            if self._origin is None:
                self._origin = (latitude, longitude)
            east, north, distance = displacement(*self._origin, latitude, longitude)
            speed_mps = (speed / 3.6) if speed is not None else None
            if self._previous is not None and speed_mps is not None and speed_mps >= MIN_MOVING_SPEED_MPS:
                self._travelled += displacement(*self._previous, latitude, longitude)[2]
            self._previous = (latitude, longitude)
            travelled = self._travelled
        else:
            # 좌표가 없으면 그 사이 경로를 알 수 없다. 누적거리를 이어 붙이지 않는다.
            self._previous = None
            if self._origin is None:
                quality = f"{quality} · 좌표 없음"
        received = self.clock.now_utc()
        monotonic = self.clock.monotonic()
        source_time = message.get("output_time_utc")
        seq = message.get("seq") or 0
        self._latest_fix = (latitude, longitude, received, monotonic, quality,
                            _number(message, ("altitude", "height")))
        values = (speed, distance, east, north, speed, travelled)
        return [SensorSample(session_id, channel, seq, received, monotonic, value, unit,
                             quality, source_time)
                for (_, channel, _, unit), value in zip(LAN_CHANNELS, values)]

    def latest_fix(self, session_id):
        """가장 최근 메시지로 만든 GpsFix. 아직 받은 것이 없으면 None."""
        latest = self._latest_fix
        if latest is None:
            return None
        latitude, longitude, received, monotonic, quality, altitude = latest
        return GpsFix(session_id, latitude, longitude, received, monotonic, quality, altitude)


class HiEdgeGpsSource:
    """LAN 스트림의 좌표를 GPS로 내보낸다.

    좌표 필드가 아직 들어오지 않으므로 지금은 위·경도가 None인 fix를 돌려준다.
    지도는 '좌표 없음'으로 표시되고, 궤적은 그려지지 않는다. 마지막 값을 현재
    위치처럼 계속 내보내지 않는다.
    """

    def __init__(self, speed_source):
        self.speed_source = speed_source

    def read(self, session_id, elapsed, scenario):
        return self.speed_source.latest_fix(session_id)


def build_sources(clock, host=DEFAULT_HOST, port=DEFAULT_PORT, certificate=None, verify=True):
    """bootstrap/presenter가 쓰는 조립 함수. (channels, sensors, gps)."""
    sensors = HiEdgeSpeedSource(clock, host, port, certificate, verify)
    return LAN_CHANNELS, sensors, HiEdgeGpsSource(sensors)
