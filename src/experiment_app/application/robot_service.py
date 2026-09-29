from copy import deepcopy
from dataclasses import replace
from threading import Event, RLock
from experiment_app.domain.robot import RobotCommand, RobotView
from experiment_app.domain.test_definition import AppError


class RobotService:
    """Session worker owns I/O; UI reads only a bounded latest status."""
    def __init__(self, transport, clock, timeout=3, stale_after=5):
        self.transport, self.clock = transport, clock
        self.timeout, self.stale_after = timeout, stale_after
        self.lock = RLock()
        self.status = RobotView(demo=transport.demo)
        self.connected = False
        self.sequence = 0
        self.received_sequence = -1
        self.last_received = None

    def view(self):
        with self.lock:
            return self.status

    def update(self, **changes):
        with self.lock:
            self.status = replace(self.status, **changes)

    def command(self, operation, cancel, payload=None):
        if cancel.is_set():
            raise AppError("ROBOT_CANCELLED", "로봇 작업이 취소되었습니다.")
        self.sequence += 1
        command = RobotCommand(self.view().session_id, self.sequence, operation, deepcopy(payload or {}))
        reply = self.transport.exchange(command, timeout=self.timeout, cancel=cancel)
        if reply.session_id != command.session_id or reply.sequence != command.sequence:
            raise AppError("ROBOT_REPLY_MISMATCH", "로봇 응답이 현재 명령과 일치하지 않습니다.")
        if not reply.accepted:
            raise AppError("ROBOT_REJECTED", reply.message or "로봇이 명령을 거부했습니다.")
        self.update(message=reply.message, last_received_utc=self.clock.now_utc())

    def begin(self, session, cancel):
        self.sequence, self.received_sequence = 0, -1
        self.last_received = None
        self.update(session_id=session.session_id, state="연결 중", message="", last_received_utc="")
        self.transport.connect(timeout=self.timeout, cancel=cancel)
        self.connected = True
        definition = session.snapshot.definition
        self.update(state="설정 전송 중")
        self.command("configure", cancel, {
            "test_id": definition.id, "test_revision": definition.revision,
            "experiment_data": definition.experiment_data,
            "ar_trapezoidal_step": definition.ar_trapezoidal_step,
            "pf_straight_line": definition.pf_straight_line,
        })
        self.update(state="설정 확인 완료")

    def start(self, cancel):
        self.command("start", cancel)
        self.last_received = self.clock.monotonic()
        self.update(state="실행 확인 완료")

    def receive(self):
        event = self.transport.receive()
        if event is not None and (event.session_id != self.view().session_id
                                  or event.sequence <= self.received_sequence):
            event = None  # late events never update current session or reset stale timeout
        if event is not None:
            self.received_sequence = event.sequence
            self.last_received = self.clock.monotonic()
            self.update(state=event.state, last_received_utc=event.received_time_utc)
            if event.state in {"error", "disconnected"}:
                raise AppError("ROBOT_DEVICE_ERROR", "로봇 오류/연결 단절 상태를 수신했습니다.")
            return event
        if self.last_received is not None and self.clock.monotonic() - self.last_received >= self.stale_after:
            raise AppError("ROBOT_STALE", "로봇 상태 수신이 지연되었습니다. 연결을 확인하세요.")
        return None

    def close(self):
        error = None
        try:
            if self.connected:
                self.update(state="중지 요청 중")
                # Acquisition cancellation must not cancel the safety stop command.
                self.command("stop", Event())
        except Exception as exception:
            error = exception
        finally:
            try:
                self.transport.disconnect(timeout=self.timeout)
            except Exception as exception:
                error = error or exception
            self.connected = False
        self.update(state="오류" if error else "연결 종료", message=str(error) if error else "")
        if error:
            raise error
