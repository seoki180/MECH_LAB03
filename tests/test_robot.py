from copy import deepcopy
from dataclasses import replace
from threading import Event
import json
import time
import pytest
from sample_tests import seeded_services as build_services
from experiment_app.application.robot_service import RobotService
from experiment_app.domain.robot import RobotEvent
from experiment_app.domain.session import State
from experiment_app.domain.test_definition import AppError, FieldPatch
from experiment_app.infrastructure.robot import DemoRobotTransport
from experiment_app.infrastructure.sources import SystemClock


class InspectTransport(DemoRobotTransport):
    def __init__(self, fail=None):
        super().__init__(SystemClock())
        self.commands = []
        self.fail = fail
        self.closed = False

    def exchange(self, command, **kwargs):
        self.commands.append(deepcopy(command))
        reply = super().exchange(command, **kwargs)
        if command.operation == self.fail:
            return replace(reply, accepted=False, message="device rejected")
        if self.fail == "wrong_reply" and command.operation == "configure":
            return replace(reply, session_id="old-session")
        return reply

    def disconnect(self, **kwargs):
        self.closed = True
        super().disconnect(**kwargs)


def finish(sessions, stop=True):
    """수집을 끝내고 결과를 돌려준다.

    가상 센서는 끝이 없으므로 기본적으로 중지 요청을 보내야 끝난다. 시작 직후에
    멈추면 로봇 명령이 아직 나가지 않아 검사가 헛돌므로, 수집이 실제로 돌기 시작한
    뒤에 중지한다. 스스로 끝나는 경우(오류)는 stop=False로 기다리기만 한다.
    """
    if stop:
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            current = sessions.current
            if current is not None and current.state == State.RUNNING and current.elapsed > 0:
                break
            if sessions.worker is None or not sessions.worker.is_alive():
                break
            time.sleep(0.01)
        if sessions.current is not None:
            sessions.stop(sessions.current.session_id)
    sessions.worker.join(4)
    assert not sessions.worker.is_alive()
    assert sessions.is_idle()
    return sessions.view()


def test_transmits_saved_snapshot_receives_and_records(tmp_path):
    transport = InspectTransport()
    main, experiment = build_services(tmp_path, robot_transport=transport)
    try:
        saved = main.service.save_patch(FieldPatch("demo-0", 1, {
            "robot/ar_trapezoidal_step/apply_rate": "12",
            "robot/pf_straight_line/start_x": "-3",
        }))
        sessions = experiment.sessions
        session = sessions.prepare(saved.id, saved.revision)
        main.service.save_patch(FieldPatch(saved.id, saved.revision, {
            "robot/pf_straight_line/start_x": "90"}))
        assert sessions.start(session.session_id)
        assert not sessions.start(session.session_id)
        result = finish(sessions)
        assert result.state == State.STOPPED
        assert [c.operation for c in transport.commands] == ["configure", "start", "stop"]
        payload = transport.commands[0].payload
        assert payload["test_revision"] == saved.revision
        assert payload["experiment_data"] == saved.experiment_data
        assert payload["ar_trapezoidal_step"]["apply_rate"] == 12
        assert payload["pf_straight_line"]["start_x"] == -3
        assert transport.closed and not transport.connected
        assert not sessions.stop(session.session_id)
        path = experiment.sessions.results.list()[0]["recording_path"]
        # 기록 파일은 UTF-8로 쓴다. 인코딩을 생략하면 Windows에서 cp1252로 읽어
        # 한글이 포함된 줄에서 UnicodeDecodeError가 난다.
        with open(path, encoding="utf-8") as recording:
            records = [json.loads(line) for line in recording]
        assert any(row.get("kind") == "robot" and row["session_id"] == session.session_id for row in records)
        assert "데모 로봇" in experiment.robot_status()
        assert sessions.robot.view().last_received_utc
    finally:
        main.dispose()


@pytest.mark.parametrize("fail,code", [("configure", "ROBOT_REJECTED"),
                                      ("wrong_reply", "ROBOT_REPLY_MISMATCH")])
def test_configuration_failure_blocks_start_and_disconnects(tmp_path, fail, code):
    """세팅은 실험 창을 열 때 보낸다. 거절당하면 시작 자체가 막힌다.

    예전에는 시작을 누른 뒤 _run 안에서 터져 ERROR 결과가 남았다. 지금은 시작
    신호가 아예 나가지 않으므로 측정도 결과도 생기지 않는다 — 로봇이 설정을
    받지 못한 것을 누르기 전에 알리는 편이 낫다.
    """
    transport = InspectTransport(fail)
    main, experiment = build_services(tmp_path, robot_transport=transport)
    try:
        sessions = experiment.sessions
        session = sessions.prepare("demo-0", 1)
        ready, reason = sessions.wait_for_robot()
        assert not ready and code in reason
        with pytest.raises(AppError) as failure:
            sessions.start(session.session_id)
        assert failure.value.code == "ROBOT_NOT_READY" and code in str(failure.value)
        assert "start" not in [c.operation for c in transport.commands]
        assert sessions.robot.view().state == "오류"
        assert sessions.results.list() == [], "시작하지 않았으므로 결과도 없다"
        # 창을 닫으면 연결을 정리한다.
        sessions.release_ready()
        assert transport.closed
    finally:
        main.dispose()


def test_stop_rejection_marks_the_result_incomplete(tmp_path):
    """중지 거부는 측정이 끝날 때 드러난다. 이 경로는 그대로다."""
    transport = InspectTransport("stop")
    main, experiment = build_services(tmp_path, robot_transport=transport)
    try:
        sessions = experiment.sessions
        session = sessions.prepare("demo-0", 1)
        assert sessions.wait_for_robot()[0]
        sessions.start(session.session_id)
        result = finish(sessions, stop=True)
        assert result.state == State.ERROR and "ROBOT_REJECTED" in result.end_reason
        assert transport.closed and sessions.robot.view().state == "오류"
        assert sessions.results.list()[0]["completeness"] == "불완전"
    finally:
        main.dispose()


def test_closing_the_window_cancels_a_pending_configuration(tmp_path):
    """세팅 전송 중에 창을 닫으면 전송을 취소한다.

    세팅은 prepare에서 나가므로, 사용자가 시작을 누르기 전에도 전송이 진행 중일 수
    있다. 그때 창을 닫으면 기다리지 않고 끊는다.
    """
    class BlockingTransport(InspectTransport):
        ready = Event()

        def exchange(self, command, *, timeout, cancel):
            if command.operation == "configure":
                self.commands.append(command)
                self.ready.set()
                assert cancel.wait(timeout)
                raise AppError("ROBOT_CANCELLED", "cancelled")
            return super().exchange(command, timeout=timeout, cancel=cancel)

    transport = BlockingTransport()
    main, experiment = build_services(tmp_path, robot_transport=transport)
    try:
        sessions = experiment.sessions
        sessions.prepare("demo-0", 1)
        assert transport.ready.wait(1), "세팅 전송이 시작된다"
        sessions.release_ready()
        assert not sessions.configuring, "창을 닫으면 전송 스레드가 끝나 있다"
        # 시작 신호는 가지 않는다. 세팅까지 간 연결은 중지로 내려놓는다
        # (HTTP 전송에는 중지 요청이 없어 아무것도 보내지 않는다).
        assert "start" not in [c.operation for c in transport.commands]
        assert transport.closed
    finally:
        main.dispose()


def test_receive_filters_old_session_and_sequence_and_detects_stale():
    class Clock:
        value = 0
        def monotonic(self): return self.value
        def now_utc(self): return "2026-09-28T00:00:00+00:00"

    class Incoming:
        demo = False
        event = None
        def receive(self):
            event, self.event = self.event, None
            return event

    clock, incoming = Clock(), Incoming()
    service = RobotService(incoming, clock)
    service.update(session_id="current")
    service.last_received = 0
    incoming.event = RobotEvent("old", 100, "running", clock.now_utc())
    assert service.receive() is None and service.received_sequence == -1
    incoming.event = RobotEvent("current", 2, "running", clock.now_utc())
    assert service.receive().sequence == 2
    incoming.event = RobotEvent("current", 1, "running", clock.now_utc())
    assert service.receive() is None and service.received_sequence == 2
    clock.value = 6
    incoming.event = RobotEvent("old", 101, "running", clock.now_utc())
    with pytest.raises(AppError) as error:
        service.receive()
    assert error.value.code == "ROBOT_STALE"
