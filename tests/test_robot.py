from copy import deepcopy
from dataclasses import replace
from threading import Event
import json
import pytest
from experiment_app.bootstrap import build_services
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


def finish(sessions):
    sessions.worker.join(4)
    assert not sessions.worker.is_alive()
    assert sessions.is_idle()
    return sessions.view()


def test_transmits_saved_snapshot_receives_and_records(tmp_path):
    transport = InspectTransport()
    main, experiment = build_services(tmp_path, duration=0.1, robot_transport=transport)
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
        assert result.state == State.COMPLETED
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
                                      ("wrong_reply", "ROBOT_REPLY_MISMATCH"),
                                      ("stop", "ROBOT_REJECTED")])
def test_command_failure_marks_incomplete_and_disconnects(tmp_path, fail, code):
    transport = InspectTransport(fail)
    main, experiment = build_services(tmp_path, duration=0.06, robot_transport=transport)
    try:
        sessions = experiment.sessions
        session = sessions.prepare("demo-0", 1)
        sessions.start(session.session_id)
        result = finish(sessions)
        assert result.state == State.ERROR and code in result.end_reason
        assert transport.closed and sessions.robot.view().state == "오류"
        assert sessions.results.list()[0]["completeness"] == "불완전"
        if fail != "stop":
            assert "start" not in [c.operation for c in transport.commands]
    finally:
        main.dispose()


def test_stop_cancels_pending_configuration_but_sends_stop(tmp_path):
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
        session = sessions.prepare("demo-0", 1)
        sessions.start(session.session_id)
        assert transport.ready.wait(1)
        sessions.stop(session.session_id)
        result = finish(sessions)
        assert result.state == State.STOPPED
        assert [c.operation for c in transport.commands] == ["configure", "stop"]
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
