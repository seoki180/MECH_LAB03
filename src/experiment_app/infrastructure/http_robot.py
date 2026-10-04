"""랜선으로 연결된 로봇 컴퓨터에 HTTP로 설정을 올리고 시험을 시작시킨다.

규격은 ``docs/ROBOT_HTTP_API.md`` 다. 이 모듈은 그 문서를 그대로 구현하며,
문서에 없는 요청을 보내지 않는다. 로봇 쪽 참고 구현은 ``tests/robot_stub.py``,
규격 검증은 ``tests/test_http_robot.py`` 에 있다.

단방향 제어 경로다
------------------
시험을 한 번 돌리면서 보내는 것은 세 가지뿐이다: 시험 세팅값, 시나리오 CSV, 시작
신호. ``/health`` 는 시험 흐름에 들어 있지 않다 — 설정 화면의 '로봇 연결 시험'에서
``probe()`` 로 한 번만 보낸다.
**진행 상태 조회와 중지 요청은 규격에 없다**(규격 §8). 그래서

- ``receive()`` 는 항상 ``None`` 이다. 로봇의 진행 상태를 받을 경로가 없다.
- ``exchange(stop)`` 은 **아무것도 보내지 않고** 성공을 돌려준다. 앱 쪽 세션을
  정리하기 위한 지역 동작이며, 로봇은 계속 구동한다.
- ``receives_status = False`` 로 그 사실을 application에 알린다. RobotService는 이
  값을 보고 '수신 지연(ROBOT_STALE)' 판정을 하지 않는다. 받을 상태가 없는데 지연을
  판정하면 모든 시험이 5초 뒤 오류로 끝난다.

구조상의 자리
-------------
``application.ports.RobotTransport`` 구현체다. 모든 호출은 세션 작업 스레드에서
일어나고 wx를 참조하지 않는다. 의미 계약(명령/응답)은 domain.robot이 정하고,
여기서는 그것을 HTTP 요청·응답으로 옮기는 일만 한다.

판단한 것들
-----------
- **POST는 자동 재시도하지 않는다.** ``/start`` 재전송은 중복 구동 위험이 있다.
  실패는 그대로 올려 사용자가 다시 시작하게 한다.
- **요청마다 새 연결을 쓴다.** 명령은 한 세션에 세 번뿐이라 연결 재사용 이득이 없고,
  오래 쉬다 끊긴 연결에 POST를 보내 실패하는 경로를 없애는 쪽이 낫다.
- **값을 지어내지 않는다.** 사용자가 입력하지 않은 값은 ``null`` 로 보낸다. 0으로
  바꾸면 '측정된 0'과 구별되지 않는다.
"""

import http.client
import json
import math
import socket
from urllib.parse import urlsplit

from experiment_app.domain.robot import RobotReply
from experiment_app.domain.scenario import ScenarioPoint, format_scenario_csv
from experiment_app.domain.test_definition import AppError

# 제안 기본값이다. 실제 로봇 주소는 설정에서 입력받는다(장비 요구사항 아님).
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
API_PREFIX = "/api/v1"

HEALTH_PATH = f"{API_PREFIX}/health"
SETTINGS_PATH = f"{API_PREFIX}/test/settings"
TARGET_PATH = f"{API_PREFIX}/test/target"
START_PATH = f"{API_PREFIX}/test/start"

JSON_TYPE = "application/json; charset=utf-8"
CSV_TYPE = "text/csv; charset=utf-8"
TARGET_FILENAME = "target.csv"

# /settings 본문은 실험 입력 데이터 전체다. 키 이름과 순서는 자료 양식 그대로다.
POINT_ANGLE_KEYS = ("Zero", "Accel", "Brake")
LIMIT_POINT_KEYS = ("Accel", "Brake")
CALIBRATION_LENGTH = 2
# 최상위 키와 순서. 규격 §6.2의 표와 같은 순서로 직렬화한다.
SETTINGS_KEYS = ("point_angle", "calibration_data", "limit_point", "zero_brake_angle")


def base_url(host, port):
    return f"http://{host}:{port}"


class HttpRobotTransport:
    """로봇 HTTP API v1 클라이언트."""

    demo = False
    # 로봇에서 진행 상태를 받지 않는다. RobotService가 수신 지연 판정을 끄는 근거다.
    receives_status = False

    def __init__(self, clock, url=None, host=DEFAULT_HOST, port=DEFAULT_PORT):
        self.clock = clock
        self.host, self.port, self.root = _split(url or base_url(host, port))
        self.connected = False
        self.started = False
        self.session_id = ""
        self.sequence = 0

    @property
    def address(self):
        return f"{base_url(self.host, self.port)}{self.root}"

    # ------------------------------------------------------------- 수명 관리

    def connect(self, *, timeout, cancel):
        """세션 시작 준비. 로봇에는 아무것도 보내지 않는다.

        예전에는 여기서 ``/health`` 를 보냈다. 지금은 보내지 않는다 — 시험을 시작할
        때 보내는 것은 세팅값(``/settings``)이 처음이고, 생존 확인은 설정 화면의
        '로봇 연결 시험'(``probe()``)에서 한 번만 한다.

        연결 가능 여부를 미리 확인하지 않으므로, 로봇이 꺼져 있으면 ``/settings``
        단계에서 ``ROBOT_UNREACHABLE`` 로 드러난다. 시작 전에 알고 싶으면 설정
        화면에서 연결 시험을 먼저 누른다.
        """
        self._check_cancel(cancel)
        self.started = False
        self.connected = True

    def probe(self, *, timeout):
        """``/health`` 를 한 번 보내 로봇이 받을 준비가 됐는지 확인한다.

        설정 화면의 '로봇 연결 시험' 전용이다. 시험 흐름에서는 호출하지 않는다.
        (성공, 표시할 문구)를 돌려주며 예외를 올리지 않는다 — 연결 확인은 실패가
        정상적인 결과 중 하나이므로 호출부가 매번 try로 감싸게 하지 않는다.
        """
        try:
            status, body = self._request("GET", HEALTH_PATH, timeout=timeout)
        except AppError as error:
            return False, str(error)
        reply = self._reply(status, body, "", 0, echo=False)
        if not reply.accepted:
            return False, f"로봇이 준비되지 않았습니다: {reply.message}"
        return True, f"연결됨 · {self.address} · {reply.message or '응답 정상'}"

    def disconnect(self, *, timeout):
        """연결을 닫는다. 로봇에는 아무것도 보내지 않는다.

        규격에 중지가 없으므로 여기서 구동이 멈추지 않는다. 시작된 시험은 로봇 쪽
        조작으로 멈춘다.
        """
        self.connected = self.started = False

    # --------------------------------------------------------------- 명령

    def exchange(self, command, *, timeout, cancel):
        self._check_cancel(cancel)
        if not self.connected:
            raise AppError("ROBOT_DISCONNECTED", "로봇에 연결되지 않았습니다.")
        self.session_id, self.sequence = command.session_id, command.sequence
        if command.operation == "configure":
            return self._configure(command, timeout, cancel)
        if command.operation == "start":
            return self._start(command, timeout)
        if command.operation == "stop":
            # 규격에 중지 요청이 없다. 앱 세션만 정리하고 로봇에는 보내지 않는다.
            # 거부로 돌리면 정상 종료가 매번 오류로 기록된다.
            self.started = False
            return RobotReply(command.session_id, command.sequence, True,
                              "앱 측정만 종료 · 로봇 중지 신호 없음(규격 미포함)")
        return RobotReply(command.session_id, command.sequence, False,
                          f"지원하지 않는 명령입니다: {command.operation}")

    def _configure(self, command, timeout, cancel):
        """시험 세팅값(JSON)과 시나리오(CSV)를 올린다. 둘 다 같은 명령 번호를 쓴다."""
        body = json.dumps(_settings_body(command.payload),
                          ensure_ascii=False, indent=4).encode("utf-8")
        status, response = self._request("POST", SETTINGS_PATH, body, JSON_TYPE,
                                         self._headers(command), timeout)
        reply = self._reply(status, response, command.session_id, command.sequence)
        if not reply.accepted:
            return reply
        points = command.payload.get("scenario")
        if not points:
            # 시나리오가 없는 시험은 /target을 보내지 않는다. 빈 CSV는 '목표값 없음'과
            # '점이 없는 곡선'을 구별할 수 없게 만든다(규격 §6.3).
            return reply
        self._check_cancel(cancel)
        csv_body = format_scenario_csv(
            [ScenarioPoint(float(time), float(target)) for time, target in points]
        ).encode("utf-8")
        headers = {**self._headers(command), "X-Filename": TARGET_FILENAME,
                   "X-Row-Count": str(len(points))}
        status, response = self._request("POST", TARGET_PATH, csv_body, CSV_TYPE,
                                         headers, timeout)
        return self._reply(status, response, command.session_id, command.sequence)

    def _start(self, command, timeout):
        body = json.dumps({"test_id": command.payload.get("test_id", ""),
                           "test_revision": command.payload.get("test_revision", 0)}).encode("utf-8")
        status, response = self._request("POST", START_PATH, body, JSON_TYPE,
                                         self._headers(command), timeout)
        reply = self._reply(status, response, command.session_id, command.sequence)
        if reply.accepted:
            self.started = True
        return reply

    # --------------------------------------------------------------- 수신

    def receive(self):
        """항상 None. 로봇의 진행 상태를 받는 경로가 규격에 없다(§8).

        여기서 그럴듯한 상태를 만들어 돌려주면 화면에는 살아 있는 로봇이 보이고
        실제로는 아무것도 확인하지 않은 것이 된다.
        """
        return None

    # ------------------------------------------------------------- 하위 구현

    @staticmethod
    def _check_cancel(cancel):
        if cancel is not None and cancel.is_set():
            raise AppError("ROBOT_CANCELLED", "로봇 작업이 취소되었습니다.")

    @staticmethod
    def _headers(command):
        return {"X-Session-Id": command.session_id,
                "X-Sequence": str(command.sequence),
                "X-Test-Id": str(command.payload.get("test_id", "")),
                "X-Test-Revision": str(command.payload.get("test_revision", 0)),
                "Accept": "application/json"}

    def _request(self, method, path, body=None, content_type=None, headers=None, timeout=None):
        """한 번만 보낸다. 명령을 자동 재시도하지 않는 이유는 모듈 설명에 있다."""
        sent = dict(headers or {})
        if content_type:
            sent["Content-Type"] = content_type
        connection = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        try:
            connection.request(method, f"{self.root}{path}", body=body, headers=sent)
            response = connection.getresponse()
            return response.status, response.read()
        except socket.timeout as error:
            raise AppError("ROBOT_TIMEOUT",
                           f"로봇이 {timeout:g}초 안에 응답하지 않았습니다 ({self.address}{path}). "
                           "로봇 프로그램이 실행 중인지 확인하세요.") from error
        except (OSError, http.client.HTTPException) as error:
            raise AppError("ROBOT_UNREACHABLE",
                           f"로봇에 연결할 수 없습니다 ({self.address}{path}): {error}. "
                           "랜선과 주소·포트를 확인하세요.") from error
        finally:
            try:
                connection.close()
            except Exception:
                pass

    def _reply(self, status, payload, session_id, sequence, echo=True):
        """응답 문서를 RobotReply로 바꾼다. 형식 위반은 성공으로 읽지 않는다."""
        document = _document(payload)
        if document is None or not isinstance(document.get("ok"), bool):
            raise AppError("ROBOT_REPLY_INVALID",
                           f"로봇 응답을 규격대로 읽을 수 없습니다 (HTTP {status}). "
                           f"받은 내용: {_preview(payload)}")
        if echo and (document.get("session_id") != session_id
                     or document.get("sequence") != sequence):
            raise AppError("ROBOT_REPLY_MISMATCH",
                           "로봇 응답의 세션/명령 번호가 보낸 것과 다릅니다. "
                           f"보냄 {session_id}/{sequence}, "
                           f"받음 {document.get('session_id')}/{document.get('sequence')}")
        if not document["ok"]:
            return RobotReply(session_id, sequence, False, _error_text(status, document))
        message = document.get("message")
        return RobotReply(session_id, sequence, True,
                          message if isinstance(message, str) else "")


def _split(url):
    parts = urlsplit(url if "://" in url else f"http://{url}")
    if parts.scheme != "http":
        raise AppError("ROBOT_PAYLOAD_INVALID", f"http 주소만 지원합니다: {url}")
    if not parts.hostname:
        raise AppError("ROBOT_PAYLOAD_INVALID", f"로봇 주소를 읽을 수 없습니다: {url}")
    root = parts.path.rstrip("/")
    return parts.hostname, parts.port or DEFAULT_PORT, root


def _settings_body(payload):
    """전송할 /settings 본문. 자료 그대로 보내고 빈 값을 0으로 바꾸지 않는다.

    네 키를 모두 담는다. 하나라도 양식에서 벗어나면 보내지 않고 거부한다. 일부만
    올리면 로봇이 어떤 설정으로 구동했는지 알 수 없게 된다.
    """
    data = payload.get("experiment_data")
    if not isinstance(data, dict):
        raise AppError("ROBOT_PAYLOAD_INVALID",
                       "시험 자료의 실험 입력 데이터를 읽을 수 없습니다. "
                       "시험 값을 확인한 뒤 다시 시작하세요.")
    missing = [key for key in SETTINGS_KEYS if key not in data]
    if missing:
        raise AppError("ROBOT_PAYLOAD_INVALID",
                       f"시험 자료에 {', '.join(missing)}이(가) 없습니다. "
                       "시험 값을 확인한 뒤 다시 시작하세요.")
    return {
        "point_angle": _point_angle(data["point_angle"]),
        "calibration_data": _pair("calibration_data", data["calibration_data"]),
        "limit_point": _limit_point(data["limit_point"]),
        "zero_brake_angle": _number("zero_brake_angle", data["zero_brake_angle"]),
    }


def _point_angle(angles):
    if not isinstance(angles, dict) or any(key not in angles for key in POINT_ANGLE_KEYS):
        raise AppError("ROBOT_PAYLOAD_INVALID",
                       "시험 자료에 point_angle(Zero/Accel/Brake)이 없습니다. "
                       "시험 값을 확인한 뒤 다시 시작하세요.")
    return {key: _pair(f"point_angle.{key}", angles[key]) for key in POINT_ANGLE_KEYS}


def _limit_point(limits):
    if not isinstance(limits, dict) or any(key not in limits for key in LIMIT_POINT_KEYS):
        raise AppError("ROBOT_PAYLOAD_INVALID",
                       "시험 자료에 limit_point(Accel/Brake)이 없습니다. "
                       "시험 값을 확인한 뒤 다시 시작하세요.")
    return {key: _number(f"limit_point.{key}", limits[key]) for key in LIMIT_POINT_KEYS}


def _pair(field, values):
    """길이 2의 배열. 길이가 다르면 보내지 않는다."""
    if not isinstance(values, (list, tuple)) or len(values) != CALIBRATION_LENGTH:
        raise AppError("ROBOT_PAYLOAD_INVALID",
                       f"{field}는 값 {CALIBRATION_LENGTH}개여야 합니다. 시험 값을 확인하세요.")
    return [_number(f"{field}[{index}]", value) for index, value in enumerate(values)]


def _number(field, value):
    """유한한 수 또는 None. NaN·Infinity는 JSON 규격이 아니므로 보내지 않는다."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AppError("ROBOT_PAYLOAD_INVALID",
                       f"{field} 값을 숫자로 읽을 수 없습니다. 시험 값을 확인하세요.")
    if not math.isfinite(value):
        raise AppError("ROBOT_PAYLOAD_INVALID",
                       f"{field} 값이 유한한 수가 아닙니다. 시험 값을 확인하세요.")
    return value


def _document(payload):
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def _error_text(status, document):
    error = (document or {}).get("error")
    if isinstance(error, dict):
        code = error.get("code") or f"HTTP_{status}"
        return f"{code}: {error.get('message') or '로봇이 명령을 거부했습니다.'}"
    return f"HTTP_{status}: 로봇이 명령을 거부했습니다."


def _preview(payload, limit=200):
    try:
        text = payload.decode("utf-8", errors="replace")
    except Exception:
        text = repr(payload)
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "") or "(빈 응답)"
