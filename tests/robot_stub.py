"""로봇 컴퓨터 쪽 참고 구현. 표준 라이브러리만 쓴다.

``docs/ROBOT_HTTP_API.md`` 규격을 그대로 구현한 **검증용 서버**다. 실제 로봇은 이
코드를 그대로 쓰지 않아도 되지만, 이 서버를 상대로 앱이 동작하면 규격을 만족한다.
반대로 로봇 쪽 구현을 만들 때 이 파일을 읽으면 어떤 검사를 해야 하는지 알 수 있다.

구현하는 것은 네 개뿐이다: ``/health``, ``/test/settings``, ``/test/target``,
``/test/start``. **진행 상태 조회와 중지는 규격에 없다**(규격 §8). 실제 로봇도
구현할 필요가 없으며, 앱은 시작 신호를 보낸 뒤 연결을 닫고 더 요청하지 않는다.

장치 구동은 하지 않는다. 받은 설정을 보관하고 상태를 바꾸는 것까지만 한다.
``fail`` 로 특정 단계를 거절하게 만들어 앱의 오류 경로를 검증한다.

직접 띄워 보기::

    python tests/robot_stub.py --port 8080
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API = "/api/v1"
POINT_ANGLE_KEYS = ("Zero", "Accel", "Brake")
LIMIT_POINT_KEYS = ("Accel", "Brake")
# /settings 본문의 최상위 키. 네 개가 모두 와야 하고 그 밖의 키는 받지 않는다.
SETTINGS_KEYS = ("point_angle", "calibration_data", "limit_point", "zero_brake_angle")
HEADER = "time,target_v"


class RobotState:
    """로봇 한 대의 상태. 현재 세션 하나만 유지한다."""

    def __init__(self, fail=None, requires_target=False, verbose=False):
        self.lock = threading.RLock()
        # 거절할 경로 이름("health" / "settings" / "target" / "start").
        self.fail = fail
        self.requires_target = requires_target
        # True면 받은 요청과 돌려준 응답을 사람이 읽을 수 있게 찍는다. 직접 띄워
        # 쓸 때는 이게 없으면 전달됐는지 알 수 없다. 테스트는 꺼 둔다.
        self.verbose = verbose
        self.state = "idle"
        self.session_id = ""
        self.sequence = 0
        self.test: tuple | None = None
        self.settings: dict | None = None
        self.target_rows: list | None = None
        # 받은 요청 기록. 테스트가 순서와 본문을 확인한다.
        self.requests: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    robot: RobotState

    # ------------------------------------------------------------- 응답 도우미

    def _send(self, status, document):
        body = json.dumps(document, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self._report(status, document)

    def _report(self, status, document):
        """무엇을 받아 무엇으로 답했는지 한 줄로 찍는다.

        받은 내용을 요약해서 보여준다. 응답 코드만 찍으면 '200이 떴는데 값이
        제대로 갔는지'를 알 수 없다.
        """
        if not self.robot.verbose:
            return
        mark = "OK  " if document.get("ok") else "FAIL"
        detail = document.get("message") or ""
        if not document.get("ok"):
            error = document.get("error") or {}
            detail = f"{error.get('code')}: {error.get('message')}"
        line = f"[{mark}] {self.command} {self.path} -> {status} {detail}"
        extra = self._summary()
        if extra:
            line += f"\n       받은 값: {extra}"
        print(line, flush=True)

    def _summary(self):
        """막 처리한 요청에서 받은 값을 사람이 확인할 수 있게 요약한다."""
        with self.robot.lock:
            if self.path.endswith("/test/settings") and self.robot.settings:
                # robot.settings에는 /settings 본문이 그대로 들어간다.
                document = self.robot.settings
                angles = document.get("point_angle") or {}
                limits = document.get("limit_point") or {}
                return ("point_angle "
                        + ", ".join(f"{key}={angles[key]}"
                                    for key in POINT_ANGLE_KEYS if key in angles)
                        + f"\n       받은 값: calibration_data={document.get('calibration_data')}"
                        + "\n       받은 값: limit_point "
                        + ", ".join(f"{key}={limits.get(key)}" for key in LIMIT_POINT_KEYS)
                        + f" · zero_brake_angle={document.get('zero_brake_angle')}")
            if self.path.endswith("/test/target") and self.robot.target_rows is not None:
                rows = self.robot.target_rows
                if not rows:
                    return "target.csv 0행"
                return (f"target.csv {len(rows)}행 "
                        f"(첫 {rows[0]}, 끝 {rows[-1]})")
            if self.path.endswith("/test/start") and self.robot.test:
                return f"시험 {self.robot.test[0]} rev {self.robot.test[1]} · 구동 상태 {self.robot.state}"
        return ""

    def log_message(self, format, *args):
        pass  # 기본 접근 로그는 쓰지 않는다. _report가 더 많은 것을 보여준다.

    def _ok(self, session_id, sequence, state, message="", **extra):
        self._send(200, {"ok": True, "session_id": session_id, "sequence": sequence,
                         "state": state, "message": message, **extra})

    def _fail(self, status, code, message, session_id="", sequence=0, state=None,
              details=None):
        document = {"ok": False, "session_id": session_id, "sequence": sequence,
                    "state": state or self.robot.state,
                    "error": {"code": code, "message": message}}
        if details:
            document["error"]["details"] = details
        self._send(status, document)

    # ------------------------------------------------------------- 요청 해석

    def _identity(self):
        """공통 헤더를 읽는다. 빠졌거나 형식이 틀리면 (None, 안내)를 돌려준다."""
        session_id = self.headers.get("X-Session-Id")
        raw_sequence = self.headers.get("X-Sequence")
        test_id = self.headers.get("X-Test-Id")
        raw_revision = self.headers.get("X-Test-Revision")
        if not session_id:
            return None, "X-Session-Id 헤더가 없습니다."
        try:
            sequence = int(raw_sequence or "")
        except ValueError:
            return None, "X-Sequence 헤더가 정수가 아닙니다."
        if test_id is None or raw_revision is None:
            return None, "X-Test-Id / X-Test-Revision 헤더가 없습니다."
        try:
            revision = int(raw_revision)
        except ValueError:
            return None, "X-Test-Revision 헤더가 정수가 아닙니다."
        return (session_id, sequence, test_id, revision), ""

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _accept_session(self, session_id, sequence):
        """세션·명령 번호 규칙(규격 §3). 통과하면 None, 아니면 (status, code, 안내)."""
        robot = self.robot
        if robot.session_id and robot.session_id != session_id:
            if robot.state == "running":
                return 409, "SESSION_CONFLICT", "다른 세션이 시험을 실행 중입니다."
            # 실행 중이 아니면 새 세션이 가져간다. 이전 세션의 설정은 버린다.
            robot.settings = robot.target_rows = robot.test = None
            robot.state = "idle"
            robot.sequence = 0
        if session_id == robot.session_id and sequence < robot.sequence:
            return 409, "SEQUENCE_REGRESSED", (
                f"명령 번호가 역행했습니다. 마지막 {robot.sequence}, 받은 {sequence}.")
        robot.session_id, robot.sequence = session_id, max(robot.sequence, sequence)
        return None

    # ------------------------------------------------------------------ 라우팅

    def do_GET(self):
        with self.robot.lock:
            self.robot.requests.append({"path": self.path.split("?")[0],
                                        "session_id": self.headers.get("X-Session-Id") or "",
                                        "sequence": self.headers.get("X-Sequence") or "",
                                        "test_id": self.headers.get("X-Test-Id") or "",
                                        "test_revision": self.headers.get("X-Test-Revision") or "",
                                        "body": b""})
        if self.path == f"{API}/health":
            return self._health()
        self._fail(404, "BAD_REQUEST", f"알 수 없는 경로입니다: {self.path}")

    def do_POST(self):
        routes = {f"{API}/test/settings": self._settings, f"{API}/test/target": self._target,
                  f"{API}/test/start": self._start}
        handler = routes.get(self.path)
        if handler is None:
            self._body()
            return self._fail(404, "BAD_REQUEST", f"알 수 없는 경로입니다: {self.path}")
        identity, problem = self._identity()
        body = self._body()
        if identity is None:
            return self._fail(400, "BAD_REQUEST", problem)
        session_id, sequence, test_id, revision = identity
        with self.robot.lock:
            self.robot.requests.append({"path": self.path, "session_id": session_id,
                                        "sequence": sequence, "test_id": test_id,
                                        "test_revision": revision, "body": body})
            rejection = self._accept_session(session_id, sequence)
            if rejection:
                status, code, message = rejection
                return self._fail(status, code, message, session_id, sequence)
            handler(session_id, sequence, test_id, revision, body)

    # ---------------------------------------------------------------- 엔드포인트

    def _health(self):
        robot = self.robot
        if robot.fail == "health":
            return self._fail(503, "DEVICE_NOT_READY", "장치가 준비되지 않았습니다. 비상정지를 해제하세요.")
        self._ok("", 0, robot.state, "robot ready")

    def _settings(self, session_id, sequence, test_id, revision, body):
        robot = self.robot
        if robot.fail == "settings":
            return self._fail(422, "SETTINGS_OUT_OF_RANGE",
                              "Accel 각도가 장비 허용 범위를 벗어났습니다. 값을 줄여 다시 보내세요.",
                              session_id, sequence)
        if robot.state == "running":
            return self._fail(409, "ALREADY_RUNNING", "실행 중에는 설정을 바꿀 수 없습니다. 먼저 중지하세요.",
                              session_id, sequence)
        kind = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if kind != "application/json":
            return self._fail(415, "UNSUPPORTED_MEDIA_TYPE",
                              f"Content-Type은 application/json이어야 합니다. 받은 값: {kind or '없음'}",
                              session_id, sequence)
        try:
            document = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            return self._fail(400, "BAD_REQUEST", f"JSON을 읽을 수 없습니다: {error}",
                              session_id, sequence)
        if not isinstance(document, dict):
            return self._fail(400, "SETTINGS_INVALID", "본문이 JSON 객체가 아닙니다.",
                              session_id, sequence)
        # 네 키가 모두 있어야 하고, 규격에 없는 최상위 키는 거절한다. 모르는 키를
        # 무시하면 설정 일부가 조용히 빠진 채로 시험이 돌아간다.
        missing = [key for key in SETTINGS_KEYS if key not in document]
        if missing:
            return self._fail(400, "SETTINGS_INVALID",
                              f"최상위 키가 없습니다: {', '.join(missing)}",
                              session_id, sequence, details={"missing": missing})
        unknown = [key for key in document if key not in SETTINGS_KEYS]
        if unknown:
            return self._fail(400, "SETTINGS_INVALID",
                              f"규격에 없는 최상위 키입니다: {', '.join(unknown)}",
                              session_id, sequence, details={"unknown": unknown})
        angles = document["point_angle"]
        if not isinstance(angles, dict):
            return self._fail(400, "SETTINGS_INVALID", "point_angle 객체가 없습니다.",
                              session_id, sequence)
        for key in POINT_ANGLE_KEYS:
            bad = self._check_pair(f"point_angle.{key}", angles.get(key))
            if bad:
                return self._fail(400, "SETTINGS_INVALID", bad[0], session_id, sequence,
                                  details=bad[1])
        bad = self._check_pair("calibration_data", document["calibration_data"])
        if bad:
            return self._fail(400, "SETTINGS_INVALID", bad[0], session_id, sequence,
                              details=bad[1])
        limits = document["limit_point"]
        if not isinstance(limits, dict) or any(key not in limits for key in LIMIT_POINT_KEYS):
            return self._fail(400, "SETTINGS_INVALID",
                              "limit_point는 Accel·Brake 두 키를 모두 가져야 합니다.",
                              session_id, sequence, details={"field": "limit_point"})
        for key in LIMIT_POINT_KEYS:
            bad = self._check_number(f"limit_point.{key}", limits[key])
            if bad:
                return self._fail(400, "SETTINGS_INVALID", bad[0], session_id, sequence,
                                  details=bad[1])
        bad = self._check_number("zero_brake_angle", document["zero_brake_angle"])
        if bad:
            return self._fail(400, "SETTINGS_INVALID", bad[0], session_id, sequence,
                              details=bad[1])
        robot.settings = document
        robot.target_rows = None
        robot.test = (test_id, revision)
        robot.state = "ready"
        self._ok(session_id, sequence, robot.state, "설정 적용 완료")

    @staticmethod
    def _check_pair(field, values):
        """길이 2의 숫자 배열인지 본다. 문제가 있으면 (문구, details)를 돌려준다."""
        if not isinstance(values, list) or len(values) != 2:
            return (f"{field}는 값 2개의 배열이어야 합니다.", {"field": field})
        for index, value in enumerate(values):
            bad = Handler._check_number(f"{field}[{index}]", value)
            if bad:
                return bad
        return None

    @staticmethod
    def _check_number(field, value):
        """유한한 수 또는 null인지 본다. null은 '아직 없음'이라 형식 오류가 아니다."""
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return (f"{field} 값을 숫자로 읽을 수 없습니다.",
                    {"field": field, "value": value})
        if value != value or value in (float("inf"), float("-inf")):
            return (f"{field} 값이 유한한 수가 아닙니다.", {"field": field})
        return None

    def _target(self, session_id, sequence, test_id, revision, body):
        robot = self.robot
        if robot.fail == "target":
            return self._fail(422, "TARGET_INVALID", "시나리오를 적재하지 못했습니다. 파일을 확인하세요.",
                              session_id, sequence)
        if robot.settings is None:
            return self._fail(409, "SETTINGS_REQUIRED", "설정을 먼저 보내세요.", session_id, sequence)
        kind = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if kind != "text/csv":
            return self._fail(415, "UNSUPPORTED_MEDIA_TYPE",
                              f"Content-Type은 text/csv여야 합니다. 받은 값: {kind or '없음'}",
                              session_id, sequence)
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as error:
            return self._fail(422, "TARGET_INVALID", f"CSV를 UTF-8로 읽을 수 없습니다: {error}",
                              session_id, sequence)
        lines = [line for line in text.replace("\r\n", "\n").split("\n") if line.strip()]
        if not lines or lines[0].strip().lstrip("\ufeff").casefold() != HEADER:
            return self._fail(422, "TARGET_INVALID",
                              f"첫 줄은 '{HEADER}' 여야 합니다. 읽은 값: {lines[0] if lines else '(빈 본문)'}",
                              session_id, sequence)
        rows, previous = [], None
        for number, line in enumerate(lines[1:], start=2):
            cells = line.split(",")
            if len(cells) < 2:
                return self._fail(422, "TARGET_INVALID", f"{number}번째 줄에 값이 부족합니다.",
                                  session_id, sequence)
            try:
                time, target = float(cells[0]), float(cells[1])
            except ValueError:
                return self._fail(422, "TARGET_INVALID", f"{number}번째 줄을 숫자로 읽을 수 없습니다.",
                                  session_id, sequence)
            if time < 0 or (previous is not None and time <= previous):
                return self._fail(422, "TARGET_INVALID",
                                  f"{number}번째 줄의 시각이 0 이상으로 증가하지 않습니다.",
                                  session_id, sequence)
            previous = time
            rows.append((time, target))
        if not rows:
            return self._fail(422, "TARGET_INVALID", "시나리오에 데이터 행이 없습니다.",
                              session_id, sequence)
        declared = self.headers.get("X-Row-Count")
        if declared is not None and declared.isdigit() and int(declared) != len(rows):
            return self._fail(422, "TARGET_ROW_COUNT_MISMATCH",
                              f"X-Row-Count {declared}와 실제 행 수 {len(rows)}가 다릅니다.",
                              session_id, sequence)
        robot.target_rows = rows
        self._ok(session_id, sequence, robot.state, f"target.csv {len(rows)}행 적재")

    def _start(self, session_id, sequence, test_id, revision, body):
        robot = self.robot
        if robot.fail == "start":
            return self._fail(503, "DEVICE_NOT_READY", "인터록이 해제되지 않았습니다. 장치를 확인하세요.",
                              session_id, sequence)
        if robot.settings is None:
            return self._fail(409, "SETTINGS_REQUIRED", "설정을 먼저 보내세요.", session_id, sequence)
        if robot.requires_target and not robot.target_rows:
            return self._fail(409, "NO_TARGET", "목표값(target.csv)이 없어 시작할 수 없습니다.",
                              session_id, sequence)
        if robot.state == "running":
            return self._fail(409, "ALREADY_RUNNING", "이미 실행 중입니다.", session_id, sequence)
        try:
            document = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, ValueError) as error:
            return self._fail(400, "BAD_REQUEST", f"JSON을 읽을 수 없습니다: {error}",
                              session_id, sequence)
        if (document.get("test_id"), document.get("test_revision")) != robot.test:
            return self._fail(409, "TEST_MISMATCH",
                              "시작 요청의 시험/버전이 설정과 다릅니다. 설정을 다시 보내세요.",
                              session_id, sequence,
                              details={"configured": list(robot.test or ()),
                                       "requested": [document.get("test_id"),
                                                     document.get("test_revision")]})
        robot.state = "running"
        self._ok(session_id, sequence, robot.state, "시험 시작")


class RobotStub:
    """스레드에서 도는 참고 서버. ``with RobotStub() as stub:`` 로 쓴다."""

    def __init__(self, fail=None, requires_target=False, host="127.0.0.1", port=0,
                 verbose=False):
        self.state = RobotState(fail=fail, requires_target=requires_target, verbose=verbose)
        handler = type("BoundHandler", (Handler,), {"robot": self.state})
        self.server = ThreadingHTTPServer((host, port), handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       name="robot-stub", daemon=True)

    @property
    def url(self):
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def port(self):
        """실제로 열린 포트. port=0으로 띄우면 OS가 고르므로 여기서 읽는다."""
        return self.server.server_address[1]

    def start(self):
        self.thread.start()
        return self

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    def __enter__(self):
        return self.start()

    def __exit__(self, *error):
        self.close()

    # 테스트 편의

    def paths(self, include_health=False):
        """받은 요청 경로 순서. /health는 연결 확인이라 기본적으로 뺀다."""
        with self.state.lock:
            return [item["path"] for item in self.state.requests
                    if include_health or not item["path"].endswith("/health")]

    def body_of(self, path):
        with self.state.lock:
            for item in reversed(self.state.requests):
                if item["path"].endswith(path):
                    return item["body"]
        return None

    def request_of(self, path):
        with self.state.lock:
            for item in reversed(self.state.requests):
                if item["path"].endswith(path):
                    return item
        return None


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="로봇 HTTP API 참고 서버")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--fail", default=None,
                        help="거절할 단계: health/settings/target/start")
    parser.add_argument("--requires-target", action="store_true",
                        help="target.csv 없이 시작을 거절한다")
    parser.add_argument("--quiet", action="store_true",
                        help="받은 요청을 찍지 않는다")
    args = parser.parse_args()
    # 직접 띄울 때는 기본으로 찍는다. 아무것도 안 보이면 전달됐는지 알 수 없다.
    try:
        stub = RobotStub(fail=args.fail, requires_target=args.requires_target,
                         host=args.host, port=args.port, verbose=not args.quiet)
    except OSError as error:
        # 추적을 쏟아내면 원인이 묻힌다. 같은 서버를 두 번 띄우는 것이 가장 흔하다.
        print(f"포트 {args.port}을 쓸 수 없습니다: {error}")
        print("이미 같은 서버가 떠 있는지 확인하세요. 확인: lsof -i:%d" % args.port)
        raise SystemExit(1)
    print(f"로봇 참고 서버 {stub.url}{API} (Ctrl+C로 종료)")
    if not args.quiet:
        print("요청을 기다립니다. 앱에서 시작을 누르면 아래에 받은 값이 찍힙니다.")
    try:
        stub.start()
        stub.thread.join()
    except KeyboardInterrupt:
        stub.close()
