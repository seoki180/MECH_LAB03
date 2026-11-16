"""HI-EDGE LAN 스트림(WebSocket) 수신 소스.

장비는 ``wss://<host>:8443/ws/data`` 로 접속하면 먼저 ``hello`` 를, 이어서 ``speed``
메시지를 명목 50 Hz로 계속 보낸다. 구독 요청은 보내지 않고 받기만 한다.

변위·이동거리·속도의 기준(SoT)
-------------------------------
**세 값 모두 장치가 보내는 것을 쓴다.** 앱이 좌표로 다시 계산하지 않는다.

- 속도: ``estimate.speed_kmh`` (최상위 ``valid`` 가 true일 때만).
- 변위: 실험을 시작할 때 **현재 위치를 장치에 기준점으로 등록**하고, 그 뒤 메시지의
  ``position_offsets[]`` 에서 우리 ``reference_id`` 항목을 찾아
  ``east_m`` / ``north_m`` / ``horizontal_distance_m`` 을 그대로 읽는다. 장치는 WGS84
  지역 ENU와 타원체 측지거리를 쓰므로 앱의 구면 근사보다 정확하고, 정의가 한 곳에만
  있다.
- 누적 이동거리: 장치는 **고정점 대비 변위만** 주고 경로 적분값은 주지 않는다.
  그래서 장치가 준 ENU(``north_m``/``east_m``)의 **차분을 앱이 적분**한다. 좌표계는
  장치 것이고 적분만 우리가 한다. 위·경도를 다시 미터로 바꾸는 일은 하지 않는다.

기준점의 수명
-------------
``prepare()`` (시작)에서 등록하고 ``close()`` (종료)에서 지운다. 기준점은 **장치 전역
공유 상태**라서 남겨 두면 다른 구독자의 목록을 어지럽히고 16칸을 잠식한다. 비정상
종료로 남은 것은 다음 시작 때 이름 규칙(``MECHLab-``)으로 찾아 지운다.

유효성은 값마다 따로 본다
-------------------------
최상위 ``valid`` 는 **속도에만** 적용된다. 위치는 ``position.valid``/``reason``,
변위는 ``position_offsets[].valid``/``reason`` 이 따로 있다. 무효인데 값이 남아 있을
수 있으므로 **값의 유무가 아니라 valid를 보고 판단한다.** 끊겼을 때 마지막 값을 현재
값으로 쓰지 않는다.
"""

import json
import math
import ssl
import threading
import time

from experiment_app.domain.telemetry import GpsFix, SensorSample
from experiment_app.domain.test_definition import AppError
from experiment_app.infrastructure import hiedge_references, hiedge_status
from experiment_app.infrastructure.streaming import StreamingSensorSource

SCHEMA = "hi-edge.speed-stream.v1"
DEFAULT_HOST = "169.254.32.88"
DEFAULT_PORT = 8443
PATH = "/ws/data"

# 장비 공개 인증서의 기본 파일 이름. 실행 파일 옆(external_root)에서 찾는다.
CERT_NAME = "hi-edge-ui-cert.pem"

# FIFO 교체로 끊겼을 때의 종료 코드. 이때는 자동 재접속을 멈춘다 — 다시 붙으면 방금
# 우리를 밀어낸 구독을 우리가 또 밀어내고, 둘이 서로를 계속 교체하게 된다.
EVICTION_CLOSE_CODE = 4001

# 기준점 16개 × 변위 항목이 실리므로 메시지가 커진다(실측 9개에 4.5 KB). 여유를 둔다.
MAX_MESSAGE_BYTES = 1 << 20

# NMEA 재생과 같은 6채널을 쓴다. 라벨만 소스를 밝힌다. 채널 id가 같아야 결과 그래프와
# 복사 대상 선택(visible)이 두 모드에서 같게 동작한다.
LAN_CHANNELS = (
    ("A", "A.0", "속도 · LAN", "km/h"),
    ("A", "A.1", "기준점 직선거리", "m"),
    ("B", "B.0", "횡방향", "m"),
    ("B", "B.1", "종방향", "m"),
    ("C", "C.0", "현재 속도", "km/h"),
    ("C", "C.1", "누적 이동거리", "m"),
)

# 이 속도 미만이면 정지로 보고 누적거리에 더하지 않는다. NMEA 재생과 같은 판정값을
# 쓴다(infrastructure/nmea.py). 실제 장비의 정지 판정 기준은 확인이 필요하다.
MIN_MOVING_SPEED_MPS = 0.3


def _number(source, keys):
    """후보 이름 중 먼저 찾은 유한한 수를 돌려준다. 없으면 None."""
    if not isinstance(source, dict):
        return None
    for key in keys:
        value = source.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return float(value)
    return None


def _section(message, name):
    """메시지 안의 하위 객체. 없거나 null이면 빈 dict."""
    section = message.get(name)
    return section if isinstance(section, dict) else {}


def _coordinates(message):
    """``position`` 이 유효할 때만 좌표를 쓴다.

    ``valid`` 가 false면 좌표 값이 남아 있어도 쓰지 않는다. 오래된 위치를 현재 위치인
    것처럼 지도에 찍으면 사용자가 둘을 구별할 수 없다.
    """
    position = _section(message, "position")
    if position.get("valid") is not True:
        return None, None
    latitude = _number(position, ("latitude_deg",))
    longitude = _number(position, ("longitude_deg",))
    if latitude is None or longitude is None:
        return None, None
    if -90 <= latitude <= 90 and -180 <= longitude <= 180:
        return latitude, longitude
    return None, None


def _altitude(message):
    """고도(m). 기준(``altitude_reference``)이 없으면 비교할 수 없으므로 쓰지 않는다."""
    position = _section(message, "position")
    if position.get("valid") is not True or not position.get("altitude_reference"):
        return None
    return _number(position, ("altitude_m",))


def _offset(message, reference_id):
    """우리 기준점의 변위 항목을 찾는다. 목록에 없으면 None.

    순서로 찾지 않는다 — 다른 구독자가 기준점을 등록·삭제하면 순서가 바뀐다.
    ``reference_id`` 로만 맞춘다.
    """
    if not reference_id:
        return None
    for entry in message.get("position_offsets") or ():
        if isinstance(entry, dict) and entry.get("reference_id") == reference_id:
            return entry
    return None


def _speed_kmh(message):
    """km/h 속도. valid가 아니거나 값이 없으면 None."""
    if message.get("valid") is not True:
        return None
    estimate = _section(message, "estimate")
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
    kind = _section(message, "estimate").get("measurement_kind")
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


def _first_speed(url, tls, timeout=6.0):
    """연결해서 hello와 speed 한 개를 받아 돌려준다. (hello, speed).

    구독 슬롯을 잠깐 쓰고 바로 닫는다(장치 전체 8개, FIFO). 연결 시험과 시작 전
    확인이 함께 쓴다.
    """
    from websockets.sync.client import connect
    secure = url.startswith("wss://")
    with connect(url, ssl=tls if secure else None, proxy=None, compression=None,
                 open_timeout=timeout, close_timeout=1, max_size=MAX_MESSAGE_BYTES) as ws:
        hello = json.loads(ws.recv(timeout=timeout))
        if hello.get("type") != "hello" or hello.get("schema") != SCHEMA:
            raise AppError("LAN_SCHEMA_MISMATCH",
                           f"예상한 HI-EDGE 속도 스트림이 아닙니다: {hello.get('schema')} / {hello.get('type')}")
        for _ in range(5):
            message = json.loads(ws.recv(timeout=timeout))
            if message.get("type") == "speed":
                return hello, message
    raise AppError("LAN_SCHEMA_MISMATCH", "hello 뒤에 speed 메시지가 오지 않았습니다.")


def _as_app_error(url, error):
    """소켓 예외를 구조화된 오류로 바꾼다. 종류 이름은 그대로 남긴다."""
    if isinstance(error, AppError):
        return error
    if isinstance(error, ssl.SSLCertVerificationError):
        return AppError("LAN_CERT_INVALID",
                        f"인증서 확인 실패: 장비의 공개 인증서와 접속 주소를 확인하세요. ({error})")
    if isinstance(error, (json.JSONDecodeError, KeyError, TypeError)):
        return AppError("LAN_SCHEMA_MISMATCH", f"메시지 형식 확인 필요: {error}")
    if isinstance(error, TimeoutError):
        return AppError("LAN_TIMEOUT", f"응답이 없습니다: {url} (이더넷 연결과 PC IP 설정을 확인하세요)")
    # websockets의 예외 계층을 여기서 열거하지 않는다. 종류 이름을 그대로 보여준다.
    return AppError("LAN_UNAVAILABLE", f"{url} 연결 실패 · {type(error).__name__}: {error}")


def probe(host=DEFAULT_HOST, port=DEFAULT_PORT, certificate=None, verify=True, timeout=6.0):
    """연결과 수신을 한 번 확인한다. 설정 화면의 '연결 시험'이 쓴다.

    GUI 스레드에서 부르면 timeout 동안 멈추므로 호출부가 작업 스레드로 돌린다.
    성공하면 사람이 읽을 요약 문구를, 실패하면 AppError를 낸다.
    """
    try:
        from websockets.sync.client import connect  # noqa: F401  (부재를 여기서 알린다)
    except ImportError as error:
        raise AppError("LAN_LIB_MISSING", "websockets 패키지가 없습니다. websockets==15.0.1을 설치하세요.") from error
    tls = tls_context(certificate, verify)
    url = stream_url(host, port)
    try:
        hello, message = _first_speed(url, tls, timeout)
    except Exception as error:
        raise _as_app_error(url, error) from error
    speed = _speed_kmh(message)
    latitude, _ = _coordinates(message)
    position = _section(message, "position")
    lines = [f"연결됨 · {url}",
             f"stream_id {hello.get('stream_id')} · 출력 {hello.get('publication_hz')} Hz"
             f" · 구독 최대 {hello.get('max_clients')}개",
             f"첫 표본 seq {message.get('seq')} · {_quality(message, speed)}",
             "속도 " + ("사용 불가(valid=false)" if speed is None else f"{speed:.3f} km/h")]
    if latitude is not None:
        lines.append(f"위치 유효 · {position.get('fix_type')} · 위성 {position.get('satellites_used')}개")
    else:
        lines.append(f"위치 무효 · {position.get('reason') or '사유 없음'}"
                     f" · {position.get('fix_type') or 'fix 없음'}"
                     " · 이 상태로는 기준점 등록과 변위 표시를 할 수 없습니다")
    lines.append(f"장치에 등록된 기준점 {len(message.get('position_offsets') or ())}개")
    return "\n".join(lines)


class HiEdgeSpeedSource(StreamingSensorSource):
    """HI-EDGE LAN 스트림을 받는 센서 소스.

    끝이 없으므로 ``continuous = True``. 사용자가 중지할 때까지 받는다.
    """

    continuous = True

    def __init__(self, clock, host=DEFAULT_HOST, port=DEFAULT_PORT,
                 certificate=None, verify=True, capacity=20000, references=None, status=None):
        # 50 Hz × 20000 ≒ 400초. 소비가 밀려도 버린 사실을 세어 남긴다.
        super().__init__(capacity=capacity)
        self.clock = clock
        self.host, self.port = host, port
        self.certificate, self.verify = certificate, verify
        self.url = stream_url(host, port)
        # 기준점 등록·삭제를 맡는 HTTP 클라이언트. None이면 기준점을 쓰지 않는다
        # (기준점 API가 없는 지역 시험 서버 상대).
        self.references = references
        # 시작 전 확인에만 쓰는 상태 조회 클라이언트(/api/latest). 측정값은 여기서
        # 읽지 않는다 — 측정의 SoT는 WebSocket이다.
        self.device_status = status
        # 마지막 확인에서 본 장치의 위치 상태. 시작 전 화면 표시에만 쓴다.
        # 측정값이 아니므로 기록하지 않는다.
        self.device_fix = None
        self.reference_id = None
        self.reference_name = ""
        self._thread = None
        self._stop = threading.Event()
        # 수신 스레드와 세션 스레드가 함께 읽는 상태. _state_lock으로만 바꾼다.
        self._state_lock = threading.Lock()
        self.status = "연결 전"
        self.last_error = ""
        self.reconnects = 0
        self.received = 0
        self.evicted = False
        # read()가 관리하는 세션별 상태. 세션 스레드만 건드린다.
        self._session_id = None
        self._previous = None
        self._travelled = 0.0
        self._latest_fix = None

    # --- 시작 전 확인 ---

    def preflight(self):
        """지금 시작하면 기준점을 등록할 수 있는지 확인한다. (가능, 사유).

        **장치 상태를 바꾸지 않고, 구독 슬롯도 쓰지 않는다.** 실험 창을 열어 둔 채
        fix를 기다리는 동안 몇 초마다 다시 불리기 때문이다. ``/ws/data`` 에 붙어서
        확인하면 그때마다 슬롯을 썼다 떼고, 슬롯이 차 있으면 확인할 때마다 다른
        구독자를 4001로 밀어낸다. 그래서 HTTP 조회(``/api/latest``)만 쓴다.

        실제 등록은 사용자가 시작을 누를 때 하며 기준점은 그 시점의 위치가 된다.
        여기서 미리 등록해 버리면 실험 창을 열었다 닫기만 해도 장치의 공유 목록이
        바뀐다.
        """
        if self.references is None:
            return True, ""
        try:
            free = self.references.free_slots()
        except AppError as error:
            return False, str(error)
        if free <= 0:
            return False, ("장치의 기준점 자리가 가득 찼습니다(최대 16개). "
                           "장치에서 쓰지 않는 기준점을 지운 뒤 다시 여세요.")
        if self.device_status is None:
            return True, ""
        try:
            latest = self.device_status.latest()
        except AppError as error:
            self.device_fix = None
            return False, str(error)
        self.device_fix = hiedge_status.snapshot(latest)
        valid, reason = hiedge_status.fix_state(latest)
        if not valid:
            return False, f"위치가 유효하지 않아 기준점을 등록할 수 없습니다 · {reason}"
        speed = hiedge_status.speed_kmh(latest)
        if speed is not None and speed > hiedge_references.MAX_REGISTER_SPEED_KMH:
            return False, (f"정차한 상태에서만 기준점을 등록할 수 있습니다"
                           f" · 현재 {speed:.2f} km/h"
                           f" (허용 {hiedge_references.MAX_REGISTER_SPEED_KMH} km/h 이하)")
        # 속도를 모를 때는 막지 않는다. INS가 초기화되지 않으면 위치해가 멀쩡해도
        # motion.valid가 false이고 speed가 null인데, **장치는 그 상태에서도 등록을
        # 받아준다**(실측 확인). 여기서 막으면 GPS가 잡혔는데도 시작할 수 없다.
        # 등록 가부의 최종 판단은 장치가 한다 — 거절하면 그 사유를 그대로 보여준다.
        return True, ""

    # --- 연결 수명 ---

    def prepare(self):
        """수신을 시작하고, 기준점이 없으면 현재 위치를 기준점으로 등록한다.

        순서가 중요하다. 소켓을 먼저 열어 수신되는 것을 확인한 뒤 등록한다. 등록이
        실패하면 열어 둔 소켓을 닫고 오류를 올린다 — 기준점 없이 시작하면 변위와
        이동거리가 빈 채로 기록되는 시험이 된다.

        **이미 가진 기준점이 장치에 남아 있으면 그대로 쓴다.** 단절로 세션이 끊긴 뒤
        다시 시작하는 경우가 그렇다. 여기서 새로 등록하면 기준점이 '다시 시작한 자리'로
        옮겨가 앞 구간의 변위와 이어지지 않는다. 기준점은 실험 창을 닫을 때까지 산다
        (``release()``).
        """
        super().prepare()
        if self.references is None:
            return
        try:
            if self.reference_id and not self.references.exists(self.reference_id):
                # 우리가 없는 동안 누가 지웠다. 이어 쓸 수 없으니 새로 잡는다.
                self.reference_id = None
            if self.reference_id:
                detail = f"기준점 {self.reference_name} 유지"
            else:
                removed = self.references.purge()
                self.reference_name = hiedge_references.new_name()
                entry = self.references.register_current(self.reference_name)
                self.reference_id = entry.get("reference_id")
                detail = f"기준점 {self.reference_name} 등록"
                if removed:
                    detail += f" · 남아 있던 {removed}개 정리"
        except AppError:
            super().close()
            raise
        with self._state_lock:
            self.status = f"{self.status} · {detail}"

    def connect(self):
        """수신 스레드를 띄운다. 첫 연결이 실패하면 시작을 막는다.

        여기서 조용히 넘기면 '시작했는데 값이 안 온다'로만 보인다. 주소·인증서·IP
        설정 문제는 시작 시점에 알려 주는 쪽이 낫다. 연결된 뒤의 단절은 스레드가
        재접속으로 처리한다.
        """
        try:
            from websockets.exceptions import ConnectionClosed
            from websockets.sync.client import connect as ws_connect
        except ImportError as error:
            raise AppError("LAN_LIB_MISSING",
                           "websockets 패키지가 없습니다. websockets==15.0.1을 설치하세요.") from error
        tls = tls_context(self.certificate, self.verify)
        self._stop.clear()
        self.evicted = False
        # 지난 연결에서 센 수신 수와 남은 오류를 기준으로 삼지 않는다. 단절로 세션이
        # 끝난 뒤 다시 시작하는 경우, 옛 오류가 남아 있으면 장치가 멀쩡히 돌아와도
        # 즉시 실패로 끝나고, 옛 수신 수를 보면 새 연결이 되기도 전에 성공으로 친다.
        with self._state_lock:
            baseline = self.received
            self.last_error = ""
        self._thread = threading.Thread(target=self._receive_loop,
                                        args=(ws_connect, tls, ConnectionClosed),
                                        name="mechlab-hiedge", daemon=True)
        self._thread.start()
        # 이번 연결의 첫 hello를 받을 때까지 기다린다. 못 받으면 시작을 거부한다.
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            with self._state_lock:
                if self.received > baseline:
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

    def close(self):
        """수신을 멈춘다. **기준점은 여기서 지우지 않는다.**

        단절로 세션이 끝난 경우에도 호출되기 때문이다. 여기서 지우면 다시 시작할 때
        기준점이 '다시 시작한 자리'로 옮겨가 앞 구간의 변위와 이어지지 않는다. 기준점을
        치우는 것은 실험 창을 닫을 때(``release()``) 한 번뿐이다.
        """
        super().close()

    def release(self):
        """실험 창을 닫을 때 이번 창에서 쓴 기준점을 지운다.

        기준점은 **장치 전역 공유 상태**라서 남겨 두면 다른 구독자의 목록에 계속 쌓이고
        16칸을 잠식한다. 삭제가 실패하면 오류를 올린다 — 호출부가 창 닫기를 막지는
        않지만 사실은 남긴다. 남은 것은 다음 등록 때 ``purge()`` 가 지운다.
        """
        reference_id = self.reference_id
        if self.references is None or not reference_id:
            return
        # 먼저 지운 것으로 표시한다. 삭제가 실패해 예외가 나가도 같은 id를 두 번
        # 지우려 들지 않는다.
        self.reference_id = None
        self.reference_name = ""
        self.references.delete(reference_id)

    def _set_status(self, status, error=""):
        with self._state_lock:
            self.status = status
            if error:
                self.last_error = error

    def _receive_loop(self, ws_connect, tls, connection_closed):
        """끊기면 1초 뒤 재접속한다. 단절 구간의 메시지는 다시 오지 않는다.

        예외 하나는 다르게 다룬다 — 4001(FIFO 교체)은 다른 프로그램이 구독을 가져간
        것이므로 재접속하지 않는다. 다시 붙으면 우리가 그 구독을 또 밀어내고 둘이
        서로를 계속 교체하게 된다.
        """
        # ws:// 는 TLS를 쓰지 않는다. 실장비는 wss:// 이고, 평문은 시험용 지역 서버뿐이다.
        secure = self.url.startswith("wss://")
        while not self._stop.is_set():
            try:
                with ws_connect(self.url, ssl=tls if secure else None, proxy=None, compression=None,
                                open_timeout=5, close_timeout=1, max_size=MAX_MESSAGE_BYTES) as ws:
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
            except connection_closed as error:
                if self._stop.is_set():
                    return
                code = error.rcvd.code if error.rcvd else 1006
                if code == EVICTION_CLOSE_CODE:
                    self.evicted = True
                    self._set_status("구독 교체됨",
                                     "다른 프로그램이 구독을 가져갔습니다(4001). 자동 재접속하지 않습니다.")
                    return
                self._wait_before_reconnect(error)
            except Exception as error:
                if self._stop.is_set():
                    return
                self._wait_before_reconnect(error)

    def _wait_before_reconnect(self, error):
        with self._state_lock:
            self.reconnects += 1
        self._set_status("재접속 대기", f"{type(error).__name__}: {error}")
        # 이전 속도를 현재 값으로 쓰면 안 된다. 큐에 아무것도 넣지 않으므로
        # 화면은 '수신 대기'로, 5초가 지나면 세션 watchdog이 단절로 끝낸다.
        self._stop.wait(1)

    # --- 세션 스레드가 꺼내 가는 부분 ---

    def _reset_session(self, session_id):
        """세션이 바뀌면 누적거리를 다시 센다.

        기준점은 장치에 등록된 것이므로 여기서 다시 잡지 않는다. 앱이 누적하는 값은
        경로 적분뿐이고, 그것만 세션마다 0에서 시작한다.
        """
        self._session_id = session_id
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
        distance = east = north = travelled = None
        offset = _offset(message, self.reference_id)
        if not self.reference_id:
            quality = f"{quality} · 기준점 없음"
            self._previous = None
        elif offset is None:
            # 등록해 둔 기준점이 목록에서 사라졌다. 다른 구독자가 지웠을 수 있다.
            quality = f"{quality} · 기준점 사라짐"
            self._previous = None
        elif offset.get("valid") is not True:
            quality = f"{quality} · 변위 {offset.get('reason') or '무효'}"
            # 변위를 모르는 구간의 경로는 알 수 없다. 누적거리를 이어 붙이지 않는다.
            self._previous = None
        else:
            east = _number(offset, ("east_m",))
            north = _number(offset, ("north_m",))
            distance = _number(offset, ("horizontal_distance_m",))
            travelled = self._accumulate(east, north, speed)
        received = self.clock.now_utc()
        monotonic = self.clock.monotonic()
        source_time = message.get("output_time_utc")
        seq = message.get("seq") or 0
        latitude, longitude = _coordinates(message)
        self._latest_fix = (latitude, longitude, received, monotonic, quality, _altitude(message))
        values = (speed, distance, east, north, speed, travelled)
        return [SensorSample(session_id, channel, seq, received, monotonic, value, unit,
                             quality, source_time)
                for (_, channel, _, unit), value in zip(LAN_CHANNELS, values)]

    def _accumulate(self, east, north, speed):
        """장치가 준 ENU 변위의 차분을 더해 누적 이동거리를 만든다.

        같은 기준점을 중심으로 한 미터 좌표이므로 두 점의 차이가 곧 이동량이다.
        정지 중 좌표 흔들림은 더하지 않는다 — 가만히 서 있는데 거리가 늘어난다.
        """
        if east is None or north is None:
            self._previous = None
            return self._travelled
        speed_mps = (speed / 3.6) if speed is not None else None
        if self._previous is not None and speed_mps is not None and speed_mps >= MIN_MOVING_SPEED_MPS:
            previous_east, previous_north = self._previous
            self._travelled += math.hypot(east - previous_east, north - previous_north)
        self._previous = (east, north)
        return self._travelled

    def latest_fix(self, session_id):
        """가장 최근 메시지로 만든 GpsFix. 아직 받은 것이 없으면 None."""
        latest = self._latest_fix
        if latest is None:
            return None
        latitude, longitude, received, monotonic, quality, altitude = latest
        return GpsFix(session_id, latitude, longitude, received, monotonic, quality, altitude)


class HiEdgeGpsSource:
    """LAN 스트림의 좌표를 GPS로 내보낸다.

    ``position.valid`` 가 false면 위·경도가 None인 fix를 돌려준다. 지도는 '좌표 없음'
    으로 표시되고 궤적은 그려지지 않는다. 마지막 값을 현재 위치처럼 계속 내보내지
    않는다.
    """

    def __init__(self, speed_source):
        self.speed_source = speed_source

    def read(self, session_id, elapsed, scenario):
        return self.speed_source.latest_fix(session_id)


def build_sources(clock, host=DEFAULT_HOST, port=DEFAULT_PORT, certificate=None, verify=True):
    """bootstrap/presenter가 쓰는 조립 함수. (channels, sensors, gps).

    기준점·상태 클라이언트는 센서와 같은 주소·TLS 설정을 쓴다. 같은 장치의 다른
    경로이므로 주소를 따로 받지 않는다.
    """
    tls = tls_context(certificate, verify)
    references = hiedge_references.ReferenceClient(host, port, tls)
    status = hiedge_status.StatusClient(host, port, tls)
    sensors = HiEdgeSpeedSource(clock, host, port, certificate, verify,
                                references=references, status=status)
    return LAN_CHANNELS, sensors, HiEdgeGpsSource(sensors)
