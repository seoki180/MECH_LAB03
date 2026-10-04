"""로봇 HTTP API 규격(docs/ROBOT_HTTP_API.md) 검증.

문서에 적은 동작을 그대로 확인한다. 상대는 ``tests/robot_stub.py`` 의 참고 서버이며
실제 소켓으로 통신한다(모의 객체가 아니다). 그래서 이 검사가 통과하면 문서대로 만든
로봇과 앱이 맞물린다고 말할 수 있다.
"""
import http.client
import json
import time

import pytest
from robot_stub import RobotStub
from sample_tests import SAMPLE_EXPERIMENT_DATA, sample_scenario, seeded_services

from experiment_app.application.robot_service import RobotService
from experiment_app.domain.robot import RobotCommand
from experiment_app.domain.session import State
from experiment_app.domain.test_definition import AppError
from experiment_app.infrastructure.http_robot import HttpRobotTransport
from experiment_app.infrastructure.sources import SystemClock


class Cancel:
    """threading.Event 대역. 취소 경로를 검사할 때만 set=True로 쓴다."""

    def __init__(self, set=False):
        self.value = set

    def is_set(self):
        return self.value


def payload(scenario=None, test_id="demo-0", revision=2):
    return {"test_id": test_id, "test_revision": revision,
            "experiment_data": SAMPLE_EXPERIMENT_DATA,
            "scenario": scenario}


def configure(transport, sequence=1, session="s" * 32, **kwargs):
    command = RobotCommand(session, sequence, "configure", payload(**kwargs))
    return transport.exchange(command, timeout=3, cancel=Cancel())


def transport_for(stub, clock=None):
    return HttpRobotTransport(clock or SystemClock(), url=stub.url)


# --------------------------------------------------------------- 1. 세팅값 전송

def test_settings_body_matches_documented_shape():
    """/settings 본문은 실험 입력 데이터 전체(최상위 네 키)이고 값은 자료 그대로다."""
    with RobotStub() as stub:
        transport = transport_for(stub)
        transport.connect(timeout=3, cancel=Cancel())
        reply = configure(transport)
        assert reply.accepted, reply.message
        request = stub.request_of("/test/settings")
        document = json.loads(request["body"].decode("utf-8"))
        assert list(document) == ["point_angle", "calibration_data",
                                  "limit_point", "zero_brake_angle"]
        assert document["point_angle"] == {
            "Zero": [0, -19], "Accel": [22, -8.3], "Brake": [-9.5, -16.6]}
        # 보정값은 자리수를 줄이지 않고 그대로 간다.
        assert document["calibration_data"] == [0.02894060757546641, -13.496798692559123]
        # 한계값과 영점 제동각은 배열이 아니라 단일 값이다.
        assert document["limit_point"] == {"Accel": -0.2, "Brake": -5}
        assert document["zero_brake_angle"] == -14
        # 공통 헤더가 모두 실린다.
        assert request["session_id"] == "s" * 32 and request["sequence"] == 1
        assert request["test_id"] == "demo-0" and request["test_revision"] == 2
        transport.disconnect(timeout=1)


def test_missing_values_are_sent_as_null_not_zero():
    """입력하지 않은 값은 null로 간다. 0으로 바꾸면 '측정된 0'과 구별되지 않는다."""
    with RobotStub() as stub:
        transport = transport_for(stub)
        transport.connect(timeout=3, cancel=Cancel())
        empty = {"point_angle": {"Zero": [None, None], "Accel": [1, None], "Brake": [None, 2]},
                 "calibration_data": [None, 0],
                 "limit_point": {"Accel": None, "Brake": 0},
                 "zero_brake_angle": None}
        command = RobotCommand("s" * 32, 1, "configure",
                               {"test_id": "demo-0", "test_revision": 2,
                                "experiment_data": empty, "scenario": None})
        assert transport.exchange(command, timeout=3, cancel=Cancel()).accepted
        document = json.loads(stub.body_of("/test/settings").decode("utf-8"))
        assert document["point_angle"] == {"Zero": [None, None], "Accel": [1, None],
                                           "Brake": [None, 2]}
        # null과 0을 서로 바꾸지 않는다.
        assert document["calibration_data"] == [None, 0]
        assert document["limit_point"] == {"Accel": None, "Brake": 0}
        assert document["zero_brake_angle"] is None
        transport.disconnect(timeout=1)


@pytest.mark.parametrize("data", [
    {},
    # point_angle만 있고 나머지 세 키가 없다.
    {"point_angle": {"Zero": [0, 0], "Accel": [0, 0], "Brake": [0, 0]}},
    # calibration_data 길이가 2가 아니다.
    {"point_angle": {"Zero": [0, 0], "Accel": [0, 0], "Brake": [0, 0]},
     "calibration_data": [1.0], "limit_point": {"Accel": 0, "Brake": 0},
     "zero_brake_angle": 0},
    # limit_point에 키가 빠졌다.
    {"point_angle": {"Zero": [0, 0], "Accel": [0, 0], "Brake": [0, 0]},
     "calibration_data": [1.0, 2.0], "limit_point": {"Accel": 0},
     "zero_brake_angle": 0},
    # 단일 값 자리에 배열이 왔다.
    {"point_angle": {"Zero": [0, 0], "Accel": [0, 0], "Brake": [0, 0]},
     "calibration_data": [1.0, 2.0], "limit_point": {"Accel": 0, "Brake": 0},
     "zero_brake_angle": [0]},
])
def test_broken_experiment_data_is_refused_before_sending(data):
    """양식에서 벗어난 시험 자료는 보내기 전에 거부한다. 빈 설정을 올리지 않는다."""
    with RobotStub() as stub:
        transport = transport_for(stub)
        transport.connect(timeout=3, cancel=Cancel())
        command = RobotCommand("s" * 32, 1, "configure",
                               {"test_id": "demo-0", "test_revision": 2,
                                "experiment_data": data, "scenario": None})
        with pytest.raises(AppError) as error:
            transport.exchange(command, timeout=3, cancel=Cancel())
        assert error.value.code == "ROBOT_PAYLOAD_INVALID"
        assert "/test/settings" not in stub.paths()
        transport.disconnect(timeout=1)


@pytest.mark.parametrize("document, expected", [
    # 규격에 없는 최상위 키는 무시하지 않고 거절한다.
    ({"point_angle": {"Zero": [0, 0], "Accel": [0, 0], "Brake": [0, 0]},
      "calibration_data": [1.0, 2.0], "limit_point": {"Accel": 0, "Brake": 0},
      "zero_brake_angle": 0, "ar_trapezoidal_step": {"a": 1}}, "SETTINGS_INVALID"),
    # 최상위 키가 빠진 본문도 거절한다.
    ({"point_angle": {"Zero": [0, 0], "Accel": [0, 0], "Brake": [0, 0]}},
     "SETTINGS_INVALID"),
    # 단일 값 자리에 배열이 오면 거절한다.
    ({"point_angle": {"Zero": [0, 0], "Accel": [0, 0], "Brake": [0, 0]},
      "calibration_data": [1.0, 2.0], "limit_point": {"Accel": [0], "Brake": 0},
      "zero_brake_angle": 0}, "SETTINGS_INVALID"),
])
def test_robot_rejects_settings_body_outside_the_spec(document, expected):
    """로봇 쪽 검증. 규격을 벗어난 /settings 본문은 400으로 거절해야 한다."""
    with RobotStub() as stub:
        connection = http.client.HTTPConnection("127.0.0.1", stub.port, timeout=3)
        try:
            connection.request("POST", "/api/v1/test/settings",
                               body=json.dumps(document).encode("utf-8"),
                               headers={"Content-Type": "application/json; charset=utf-8",
                                        "X-Session-Id": "s" * 32, "X-Sequence": "1",
                                        "X-Test-Id": "demo-0", "X-Test-Revision": "2"})
            response = connection.getresponse()
            status, body = response.status, json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()
        assert status == 400
        assert body["ok"] is False and body["error"]["code"] == expected
        # 거절해도 echo 규칙은 지킨다.
        assert body["session_id"] == "s" * 32 and body["sequence"] == 1


# ------------------------------------------------------------- 2. target.csv 전송

def test_target_csv_is_sent_with_same_sequence_and_row_count():
    """시나리오는 설정과 같은 명령 번호로, 문서 양식의 CSV 본문으로 간다."""
    points = [[point.time, point.target_v] for point in sample_scenario()]
    with RobotStub() as stub:
        transport = transport_for(stub)
        transport.connect(timeout=3, cancel=Cancel())
        assert configure(transport, scenario=points).accepted
        assert stub.paths() == ["/api/v1/test/settings", "/api/v1/test/target"]
        request = stub.request_of("/test/target")
        body = request["body"].decode("utf-8")
        assert body.startswith("time,target_v\r\n")
        assert body.count("\r\n") == len(points) + 1
        # 같은 configure 명령이므로 번호가 같다.
        assert request["sequence"] == 1
        assert stub.state.target_rows is not None
        assert len(stub.state.target_rows) == len(points)
        transport.disconnect(timeout=1)


def test_no_scenario_sends_no_target_request():
    """시나리오가 없으면 /target을 아예 보내지 않는다(빈 CSV를 보내지 않는다)."""
    with RobotStub() as stub:
        transport = transport_for(stub)
        transport.connect(timeout=3, cancel=Cancel())
        assert configure(transport, scenario=None).accepted
        assert stub.paths() == ["/api/v1/test/settings"]
        transport.disconnect(timeout=1)


def test_target_required_robot_refuses_start_without_scenario():
    """목표값이 필요한 로봇은 NO_TARGET으로 시작을 거절하고, 앱은 그 문구를 전달한다."""
    with RobotStub(requires_target=True) as stub:
        transport = transport_for(stub)
        transport.connect(timeout=3, cancel=Cancel())
        assert configure(transport, scenario=None).accepted
        reply = transport.exchange(RobotCommand("s" * 32, 2, "start", payload()),
                                   timeout=3, cancel=Cancel())
        assert not reply.accepted and "NO_TARGET" in reply.message
        transport.disconnect(timeout=1)


# ----------------------------------------------------------------- 3. 시작/중지

def test_start_only_runs_after_settings_and_echoes_identity():
    with RobotStub() as stub:
        transport = transport_for(stub)
        transport.connect(timeout=3, cancel=Cancel())
        # 설정 없이 시작하면 거절된다.
        early = transport.exchange(RobotCommand("s" * 32, 1, "start", payload()),
                                   timeout=3, cancel=Cancel())
        assert not early.accepted and "SETTINGS_REQUIRED" in early.message
        assert stub.state.state == "idle"
        assert configure(transport, sequence=2).accepted
        assert stub.state.state == "ready"
        started = transport.exchange(RobotCommand("s" * 32, 3, "start", payload()),
                                     timeout=3, cancel=Cancel())
        assert started.accepted
        assert stub.state.state == "running"
        body = json.loads(stub.body_of("/test/start").decode("utf-8"))
        assert body == {"test_id": "demo-0", "test_revision": 2}
        transport.disconnect(timeout=1)


def test_start_with_other_test_is_refused():
    with RobotStub() as stub:
        transport = transport_for(stub)
        transport.connect(timeout=3, cancel=Cancel())
        assert configure(transport, sequence=1).accepted
        reply = transport.exchange(
            RobotCommand("s" * 32, 2, "start", payload(test_id="demo-1", revision=9)),
            timeout=3, cancel=Cancel())
        assert not reply.accepted and "TEST_MISMATCH" in reply.message
        assert stub.state.state == "ready"
        transport.disconnect(timeout=1)


def test_stop_sends_nothing_to_the_robot_but_reports_success():
    """중지 요청은 규격에 없다. 로봇에 아무것도 보내지 않고 앱 세션만 정리한다.

    여기서 실패를 돌려주면 정상 종료가 매번 오류로 기록된다. 반대로 요청을 보내면
    규격에 없는 경로를 두드려 404가 온다.
    """
    with RobotStub() as stub:
        transport = transport_for(stub)
        transport.connect(timeout=3, cancel=Cancel())
        assert configure(transport, sequence=1).accepted
        assert transport.exchange(RobotCommand("s" * 32, 2, "start", payload()),
                                  timeout=3, cancel=Cancel()).accepted
        before = stub.paths()
        reply = transport.exchange(RobotCommand("s" * 32, 3, "stop", payload()),
                                   timeout=3, cancel=Cancel())
        assert reply.accepted
        assert "로봇 중지 신호 없음" in reply.message
        assert stub.paths() == before  # 요청이 늘지 않았다
        # 로봇은 여전히 구동 중이다. 앱이 멈출 수단이 없다는 사실을 그대로 둔다.
        assert stub.state.state == "running"
        transport.disconnect(timeout=1)
        assert stub.state.state == "running"


def test_sequence_regression_is_refused():
    with RobotStub() as stub:
        transport = transport_for(stub)
        transport.connect(timeout=3, cancel=Cancel())
        assert configure(transport, sequence=5).accepted
        reply = configure(transport, sequence=4)
        assert not reply.accepted and "SEQUENCE_REGRESSED" in reply.message
        transport.disconnect(timeout=1)


def test_other_session_cannot_touch_a_running_robot():
    with RobotStub() as stub:
        transport = transport_for(stub)
        transport.connect(timeout=3, cancel=Cancel())
        assert configure(transport, sequence=1).accepted
        assert transport.exchange(RobotCommand("s" * 32, 2, "start", payload()),
                                  timeout=3, cancel=Cancel()).accepted
        other = transport_for(stub)
        other.connect(timeout=3, cancel=Cancel())
        reply = configure(other, sequence=1, session="o" * 32)
        assert not reply.accepted and "SESSION_CONFLICT" in reply.message
        transport.disconnect(timeout=1)
        other.disconnect(timeout=1)


# ------------------------------------------------- 4. 상태 조회가 없다는 사실

def test_no_status_or_stop_requests_are_ever_sent():
    """규격에 없는 경로를 두드리지 않는다. 시험 한 번에 보내는 것은 세 개뿐이다."""
    points = [[p.time, p.target_v] for p in sample_scenario()]
    with RobotStub() as stub:
        transport = transport_for(stub)
        transport.connect(timeout=3, cancel=Cancel())
        assert configure(transport, sequence=1, scenario=points).accepted
        assert transport.exchange(RobotCommand("s" * 32, 2, "start", payload()),
                                  timeout=3, cancel=Cancel()).accepted
        for _ in range(5):
            assert transport.receive() is None  # 받을 상태가 없다
        transport.exchange(RobotCommand("s" * 32, 3, "stop", payload()),
                           timeout=3, cancel=Cancel())
        transport.disconnect(timeout=1)
        paths = stub.paths(include_health=True)
        # /health도 없다. 연결 확인은 설정 화면의 '로봇 연결 시험'에서만 보낸다.
        assert paths == ["/api/v1/test/settings", "/api/v1/test/target",
                         "/api/v1/test/start"]
        assert not any("status" in path or "stop" in path or "health" in path
                       for path in paths)


def test_transport_declares_that_it_receives_no_status():
    """RobotService가 수신 지연 판정을 끄는 근거다. 없으면 모든 시험이 오류가 된다."""
    transport = HttpRobotTransport(SystemClock(), url="http://127.0.0.1:1")
    assert transport.receives_status is False
    service = RobotService(transport, SystemClock())
    assert service.receives_status is False
    # 데모 전송은 상태를 보내므로 판정을 켠 채로 둔다.
    from experiment_app.infrastructure.robot import DemoRobotTransport
    assert RobotService(DemoRobotTransport(SystemClock()), SystemClock()).receives_status


def test_no_status_transport_never_raises_stale():
    """받을 상태가 없는 전송에서는 시간이 아무리 지나도 ROBOT_STALE이 나오지 않는다."""
    class Clock:
        value = 0.0
        def monotonic(self): return self.value
        def now_utc(self): return "2026-10-02T00:00:00+00:00"

    clock = Clock()
    transport = HttpRobotTransport(clock, url="http://127.0.0.1:1")
    service = RobotService(transport, clock, stale_after=5)
    service.update(session_id="s" * 32)
    service.identity = {"test_id": "demo-0", "test_revision": 2}
    service.connected = True
    # start()가 수신 시계를 켜지 않는다.
    transport.connected = True
    service.last_received = None
    clock.value = 10_000
    assert service.receive() is None  # 예외 없음


# -------------------------------------------------------------- 5. 오류/규격 위반

def test_unreachable_and_timeout_are_distinct_codes():
    """연결 실패는 connect가 아니라 첫 요청(/settings)에서 드러난다.

    connect는 더 이상 로봇에 아무것도 보내지 않는다(시험 흐름에 /health가 없다).
    그래서 로봇이 꺼져 있으면 세팅값을 보내는 순간 알게 된다.
    """
    transport = HttpRobotTransport(SystemClock(), url="http://127.0.0.1:9")
    transport.connect(timeout=1, cancel=Cancel())  # 아무것도 보내지 않으므로 성공한다
    with pytest.raises(AppError) as error:
        configure(transport)
    assert error.value.code == "ROBOT_UNREACHABLE"
    assert "랜선" in str(error.value)


def test_connect_sends_nothing_to_the_robot():
    """시험 시작의 첫 요청은 /settings다. /health를 보내지 않는다."""
    with RobotStub() as stub:
        transport = transport_for(stub)
        transport.connect(timeout=3, cancel=Cancel())
        assert stub.paths(include_health=True) == []  # 아직 아무 요청도 없다
        assert transport.connected
        assert configure(transport, sequence=1).accepted
        assert stub.paths(include_health=True) == ["/api/v1/test/settings"]
        transport.disconnect(timeout=1)


def test_probe_checks_health_only_from_the_settings_screen():
    """설정 화면의 연결 시험만 /health를 보낸다. 예외를 올리지 않는다."""
    with RobotStub() as stub:
        transport = transport_for(stub)
        ok, message = transport.probe(timeout=3)
        assert ok and stub.url in message
        assert stub.paths(include_health=True) == ["/api/v1/health"]
    # 로봇이 준비되지 않았다고 답하면 실패로 돌려준다(예외가 아니다).
    with RobotStub(fail="health") as stub:
        ok, message = transport_for(stub).probe(timeout=3)
        assert not ok and "DEVICE_NOT_READY" in message
    # 꺼져 있는 주소도 예외 없이 사유를 돌려준다.
    ok, message = HttpRobotTransport(SystemClock(), url="http://127.0.0.1:9").probe(timeout=1)
    assert not ok and "랜선" in message


def test_health_failure_surfaces_when_the_test_starts():
    """연결 시험을 누르지 않았다면 /settings 단계에서 드러난다."""
    with RobotStub(fail="health") as stub:
        transport = transport_for(stub)
        transport.connect(timeout=3, cancel=Cancel())
        # /health를 보내지 않으므로 거절 설정과 무관하게 세팅값은 통과한다.
        assert configure(transport).accepted
        assert "/api/v1/health" not in stub.paths(include_health=True)
        transport.disconnect(timeout=1)


def test_cancel_before_command_raises_cancelled():
    with RobotStub() as stub:
        transport = transport_for(stub)
        transport.connect(timeout=3, cancel=Cancel())
        with pytest.raises(AppError) as error:
            transport.exchange(RobotCommand("s" * 32, 1, "configure", payload()),
                               timeout=3, cancel=Cancel(set=True))
        assert error.value.code == "ROBOT_CANCELLED"
        assert "/api/v1/test/settings" not in stub.paths()
        transport.disconnect(timeout=1)


def test_settings_rejection_message_reaches_the_user():
    with RobotStub(fail="settings") as stub:
        transport = transport_for(stub)
        transport.connect(timeout=3, cancel=Cancel())
        reply = configure(transport)
        assert not reply.accepted
        assert "SETTINGS_OUT_OF_RANGE" in reply.message and "허용 범위" in reply.message
        transport.disconnect(timeout=1)


def test_echo_mismatch_is_not_read_as_success():
    """session_id/sequence를 되돌려주지 않는 로봇의 응답은 믿지 않는다."""
    transport = HttpRobotTransport(SystemClock(), url="http://127.0.0.1:1")
    with pytest.raises(AppError) as error:
        transport._reply(200, json.dumps(
            {"ok": True, "session_id": "other", "sequence": 99, "state": "ready"}
        ).encode("utf-8"), "s" * 32, 1)
    assert error.value.code == "ROBOT_REPLY_MISMATCH"


def test_non_json_reply_is_reply_invalid():
    transport = HttpRobotTransport(SystemClock(), url="http://127.0.0.1:1")
    with pytest.raises(AppError) as error:
        transport._reply(200, b"<html>robot</html>", "s" * 32, 1)
    assert error.value.code == "ROBOT_REPLY_INVALID"
    assert "robot" in str(error.value)


# ------------------------------------------------- 6. 세션 전체 흐름(통합)

def test_full_session_sends_settings_target_start_only(tmp_path):
    """시작을 누르면 세팅 → 시나리오 → 시작 순으로 가고, 그 뒤 요청이 없다.

    앱 측정을 중지해도 로봇에는 아무것도 가지 않으며, 그래도 앱 쪽 세션은 정상
    종료(STOPPED)로 끝난다. 규격에 중지가 없다는 사실이 앱 기록을 오류로 만들면 안 된다.
    """
    with RobotStub() as stub:
        clock = SystemClock()
        transport = HttpRobotTransport(clock, url=stub.url)
        main, experiment = seeded_services(tmp_path, robot_transport=transport)
        try:
            sessions = experiment.sessions
            session = sessions.prepare("demo-0", 1)
            assert sessions.start(session.session_id)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                current = sessions.current
                if current is not None and current.state == State.RUNNING and current.elapsed > 0:
                    break
                if sessions.worker is None or not sessions.worker.is_alive():
                    break
                time.sleep(0.01)
            assert stub.state.state == "running"
            sessions.stop(session.session_id)
            sessions.worker.join(5)
            assert not sessions.worker.is_alive()
            result = sessions.view()
            assert result.state == State.STOPPED, result.end_reason
            assert stub.paths() == ["/api/v1/test/settings", "/api/v1/test/target",
                                    "/api/v1/test/start"]
            # 앱이 중지해도 로봇은 계속 구동한다. 규격 §8의 결과를 그대로 확인한다.
            assert stub.state.state == "running"
            # 같은 세션 식별자가 모든 명령 요청에 실린다(/health는 세션 전이다).
            sessions_seen = {item["session_id"] for item in stub.state.requests
                             if not item["path"].endswith("/health")}
            assert sessions_seen == {session.session_id}
            # 상태를 받지 않으므로 기록에 로봇 진행이 남지 않는다.
            path = sessions.results.list()[0]["recording_path"]
            with open(path, encoding="utf-8") as recording:
                records = [json.loads(line) for line in recording]
            assert not any(row.get("kind") == "robot" for row in records)
            # 측정 자체는 남는다(로봇 상태만 없는 것이다).
            assert records
        finally:
            main.dispose()


def test_configure_rejection_stops_before_start(tmp_path):
    """세팅을 거절당하면 시작 신호가 나가지 않는다.

    세팅은 실험 창을 열 때(prepare) 보내므로, 거절도 시작을 누르기 전에 드러난다.
    """
    with RobotStub(fail="settings") as stub:
        transport = HttpRobotTransport(SystemClock(), url=stub.url)
        main, experiment = seeded_services(tmp_path, robot_transport=transport)
        try:
            sessions = experiment.sessions
            session = sessions.prepare("demo-0", 1)
            ready, reason = sessions.wait_for_robot()
            assert not ready and "ROBOT_REJECTED" in reason
            with pytest.raises(AppError) as failure:
                sessions.start(session.session_id)
            assert failure.value.code == "ROBOT_NOT_READY"
            # 거절되면 시작 요청을 보내지 않는다. 로봇은 구동하지 않은 상태로 남는다.
            assert "/api/v1/test/start" not in stub.paths()
            assert stub.state.state == "idle"
            assert sessions.results.list() == [], "시작하지 않았으므로 결과도 없다"
        finally:
            main.dispose()


def test_long_session_stays_running_without_status(tmp_path):
    """상태 수신이 없어도 시험이 중간에 오류로 끊기지 않는다.

    stale 판정이 켜져 있었다면 stale_after를 넘기는 순간 ROBOT_STALE로 끝난다.
    """
    with RobotStub() as stub:
        transport = HttpRobotTransport(SystemClock(), url=stub.url)
        main, experiment = seeded_services(tmp_path, robot_transport=transport)
        try:
            sessions = experiment.sessions
            # 판정 창을 짧게 줄여 놓는다. 켜져 있으면 바로 드러난다.
            sessions.robot = RobotService(transport, SystemClock(), stale_after=0.3)
            session = sessions.prepare("demo-0", 1)
            sessions.start(session.session_id)
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline:
                assert sessions.worker.is_alive(), sessions.view().end_reason
                time.sleep(0.05)
            assert sessions.view().state == State.RUNNING
            sessions.stop(session.session_id)
            sessions.worker.join(5)
            assert sessions.view().state == State.STOPPED, sessions.view().end_reason
        finally:
            main.dispose()


# -------------------------------------------------- 7. 전송 선택과 설정 보존

def test_robot_url_selects_http_transport_and_absence_keeps_demo(tmp_path):
    """주소가 있으면 HTTP 전송, 없으면 데모다. 주소를 추측해 붙지 않는다."""
    from experiment_app.bootstrap import build_services
    from experiment_app.infrastructure.robot import DemoRobotTransport

    main, experiment = build_services(tmp_path / "demo")
    try:
        assert isinstance(experiment.sessions.robot.transport, DemoRobotTransport)
        assert "데모 로봇" in experiment.robot_status()
    finally:
        main.dispose()
    main, experiment = build_services(tmp_path / "http", robot_url="http://192.0.2.10:9000")
    try:
        transport = experiment.sessions.robot.transport
        assert isinstance(transport, HttpRobotTransport)
        assert transport.address == "http://192.0.2.10:9000"
        assert not transport.demo
        # 데모가 아니면 상태 문구에서 '데모' 표시가 빠진다.
        assert "데모" not in experiment.robot_status()
    finally:
        main.dispose()


def test_saved_robot_settings_are_used_only_when_enabled(tmp_path):
    from experiment_app.bootstrap import build_services
    from experiment_app.infrastructure.robot import DemoRobotTransport
    from experiment_app.infrastructure.settings_store import SettingsStore

    store = SettingsStore(tmp_path / "settings.json")
    store.save_robot({"host": "192.0.2.77", "port": 8123, "enabled": False})
    main, experiment = build_services(tmp_path)
    try:
        assert isinstance(experiment.sessions.robot.transport, DemoRobotTransport)
    finally:
        main.dispose()
    store.save_robot({"host": "192.0.2.77", "port": 8123, "enabled": True})
    main, experiment = build_services(tmp_path)
    try:
        assert experiment.sessions.robot.transport.address == "http://192.0.2.77:8123"
    finally:
        main.dispose()
    # 로봇 설정을 저장해도 LAN 설정을 건드리지 않는다.
    store.save_lan({"host": "169.254.1.1", "port": 8443, "verify": False})
    store.save_robot({"host": "192.0.2.78", "port": 8080, "enabled": True})
    assert store.lan({"host": "x", "port": 1, "verify": True})["host"] == "169.254.1.1"
    assert store.robot({"host": "x", "port": 1, "enabled": False})["host"] == "192.0.2.78"


def test_broken_robot_url_is_refused_with_a_clear_code():
    with pytest.raises(AppError) as error:
        HttpRobotTransport(SystemClock(), url="wss://192.0.2.10:8443")
    assert error.value.code == "ROBOT_PAYLOAD_INVALID"


# ------------------------------------------- 8. 설정 화면에서 주소 바꾸기

def test_settings_screen_switches_the_robot_and_persists_it(tmp_path):
    """설정의 '로봇 주소 적용'이 실제 전송을 바꾸고 다음 실행까지 남긴다."""
    from experiment_app.bootstrap import build_services
    from experiment_app.infrastructure.robot import DemoRobotTransport

    with RobotStub() as stub:
        main, experiment = build_services(tmp_path)
        try:
            # 처음엔 데모다. 주소를 추측해 붙지 않는다.
            assert isinstance(experiment.sessions.robot.transport, DemoRobotTransport)
            experiment.use_robot(host="127.0.0.1", port=stub.port, enabled=True)
            transport = experiment.sessions.robot.transport
            assert isinstance(transport, HttpRobotTransport)
            assert transport.address == stub.url
            assert not experiment.robot_save_error
        finally:
            main.dispose()
        # 다음 실행에서 저장된 주소를 그대로 쓴다.
        main, experiment = build_services(tmp_path)
        try:
            assert experiment.sessions.robot.transport.address == stub.url
            assert experiment.robot_settings["enabled"] is True
        finally:
            main.dispose()


def test_turning_the_robot_off_returns_to_demo(tmp_path):
    """체크를 끄면 데모로 돌아간다. 주소는 남지만 실제로 보내지 않는다."""
    from experiment_app.bootstrap import build_services
    from experiment_app.infrastructure.robot import DemoRobotTransport

    main, experiment = build_services(tmp_path)
    try:
        experiment.use_robot(host="192.0.2.50", port=8080, enabled=True)
        assert not experiment.sessions.robot.transport.demo
        experiment.use_robot(host="192.0.2.50", port=8080, enabled=False)
        assert isinstance(experiment.sessions.robot.transport, DemoRobotTransport)
        assert "데모" in experiment.robot_status()
        # 주소는 보존된다. 다시 켤 때 다시 입력하지 않는다.
        assert experiment.robot_settings["host"] == "192.0.2.50"
    finally:
        main.dispose()
    main, experiment = build_services(tmp_path)
    try:
        assert experiment.robot_settings["host"] == "192.0.2.50"
        assert experiment.robot_settings["enabled"] is False
        assert isinstance(experiment.sessions.robot.transport, DemoRobotTransport)
    finally:
        main.dispose()


def test_enabling_the_robot_without_an_address_is_refused(tmp_path):
    """빈 주소로 켜지 못한다. 켜졌다면 시작할 때 비로소 실패한다."""
    from experiment_app.bootstrap import build_services

    main, experiment = build_services(tmp_path)
    try:
        with pytest.raises(AppError) as error:
            experiment.use_robot(host="  ", port=8080, enabled=True)
        assert error.value.code == "VALIDATION_FAILED"
        # 거부되면 전송이 바뀌지 않는다.
        assert experiment.sessions.robot.transport.demo
        assert not experiment.robot_settings["enabled"]
    finally:
        main.dispose()


def test_robot_cannot_be_swapped_while_a_session_is_active(tmp_path):
    """실행 중 교체를 막는다. 어느 로봇이 구동하는지 알 수 없게 되면 안 된다."""
    with RobotStub() as stub:
        transport = HttpRobotTransport(SystemClock(), url=stub.url)
        main, experiment = seeded_services(tmp_path, robot_transport=transport)
        try:
            sessions = experiment.sessions
            session = sessions.prepare("demo-0", 1)
            sessions.start(session.session_id)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and sessions.view().state != State.RUNNING:
                time.sleep(0.01)
            with pytest.raises(AppError) as error:
                experiment.use_robot(host="192.0.2.99", port=8080, enabled=True)
            assert error.value.code == "INVALID_STATE"
            # 거부돼도 실행 중인 전송은 그대로다.
            assert sessions.robot.transport is transport
            sessions.stop(session.session_id)
            sessions.worker.join(5)
        finally:
            main.dispose()


def test_command_line_url_shows_up_in_the_settings_screen(tmp_path):
    """--robot-url로 준 주소가 설정 화면 값과 어긋나지 않는다."""
    from experiment_app.bootstrap import build_services

    main, experiment = build_services(tmp_path, robot_url="http://192.0.2.10:9100")
    try:
        assert experiment.robot_settings == {"host": "192.0.2.10", "port": 9100, "enabled": True}
    finally:
        main.dispose()


def test_settings_go_out_when_the_window_opens_and_start_when_the_button_is_pressed(tmp_path):
    """요청 시점이 두 군데로 나뉜다.

    실험 창을 열면(prepare) 세팅값과 target.csv가 나가고, 시작을 누를 때 start만
    나간다. 세팅을 미리 보내 두는 이유는 시작 버튼을 누른 뒤 로봇이 설정을 받고
    검증하는 시간만큼 구동이 늦어지지 않게 하기 위해서다.
    """
    with RobotStub() as stub:
        transport = HttpRobotTransport(SystemClock(), url=stub.url)
        main, experiment = seeded_services(tmp_path, robot_transport=transport)
        try:
            sessions = experiment.sessions
            session = sessions.prepare("demo-0", 1)
            assert sessions.wait_for_robot()[0]

            # 창을 연 시점: 세팅과 시나리오는 갔고 시작 신호는 아직이다.
            after_open = stub.paths()
            assert "/api/v1/test/settings" in after_open
            assert "/api/v1/test/target" in after_open
            assert "/api/v1/test/start" not in after_open
            assert stub.state.state == "ready", "설정만 받았고 아직 구동하지 않는다"

            # 시작을 누른 시점: start만 더 나간다.
            assert sessions.start(session.session_id)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and "/api/v1/test/start" not in stub.paths():
                time.sleep(0.02)
            assert stub.paths() == after_open + ["/api/v1/test/start"]
            assert stub.state.state == "running"

            sessions.stop(session.session_id)
            sessions.worker.join(5)
        finally:
            main.dispose()


def test_save_failure_is_reported_but_keeps_the_applied_address(tmp_path):
    """저장 실패가 적용을 되돌리지 않는다. 사실대로 알리기만 한다."""
    from experiment_app.bootstrap import build_services

    main, experiment = build_services(tmp_path)
    try:
        def fail(settings):
            raise OSError("디스크 쓰기 거부")

        experiment.settings_store.save_robot = fail
        experiment.use_robot(host="192.0.2.60", port=8080, enabled=True)
        # 이번 실행에는 반영됐다.
        assert experiment.sessions.robot.transport.address == "http://192.0.2.60:8080"
        assert "저장하지 못해" in experiment.robot_save_error
    finally:
        main.dispose()


def test_settings_screen_probe_uses_the_typed_address(tmp_path):
    """연결 시험은 입력된 주소로 한다. 아직 적용하지 않은 주소도 확인할 수 있다."""
    from experiment_app.bootstrap import build_services
    from experiment_app.infrastructure.robot import DemoRobotTransport

    with RobotStub() as stub:
        main, experiment = build_services(tmp_path)
        try:
            # 적용하지 않은 상태(데모)에서도 시험이 된다.
            assert isinstance(experiment.sessions.robot.transport, DemoRobotTransport)
            ok, message = experiment.probe_robot("127.0.0.1", stub.port)
            assert ok and stub.url in message
            assert stub.paths(include_health=True) == ["/api/v1/health"]
            # 시험만으로 전송이 바뀌지는 않는다. 적용은 '주소 적용'이 한다.
            assert isinstance(experiment.sessions.robot.transport, DemoRobotTransport)
            # 주소를 비우면 소켓을 열지 않고 거부한다.
            ok, message = experiment.probe_robot("  ", stub.port)
            assert not ok and "주소를 입력" in message
            assert stub.paths(include_health=True) == ["/api/v1/health"]
        finally:
            main.dispose()


def test_starting_a_test_never_sends_health(tmp_path):
    """시험 흐름에 /health가 없다. 세팅값이 첫 요청이고 시작 신호가 마지막이다."""
    with RobotStub() as stub:
        transport = HttpRobotTransport(SystemClock(), url=stub.url)
        main, experiment = seeded_services(tmp_path, robot_transport=transport)
        try:
            sessions = experiment.sessions
            session = sessions.prepare("demo-0", 1)
            sessions.start(session.session_id)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and sessions.view().state != State.RUNNING:
                time.sleep(0.01)
            sessions.stop(session.session_id)
            sessions.worker.join(5)
            assert sessions.view().state == State.STOPPED, sessions.view().end_reason
            assert stub.paths(include_health=True) == ["/api/v1/test/settings",
                                                       "/api/v1/test/target",
                                                       "/api/v1/test/start"]
        finally:
            main.dispose()
