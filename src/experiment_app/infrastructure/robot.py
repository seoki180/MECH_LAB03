"""In-process demo; replace with a real SDK/TCP/serial/CAN adapter."""
from copy import deepcopy
from experiment_app.domain.robot import RobotReply, RobotEvent
from experiment_app.domain.test_definition import AppError


class DemoRobotTransport:
    demo = True

    def __init__(self, clock):
        self.clock = clock
        self.connected = False
        self.running = False
        self.configuration = None
        self.session_id = ""
        self.sequence = 0
        self.next_status = 0

    def connect(self, *, timeout, cancel):
        if cancel.is_set():
            raise AppError("ROBOT_CANCELLED", "데모 연결 취소")
        self.connected = True
        self.configuration = None
        self.running = False
        self.sequence = 0

    def exchange(self, command, *, timeout, cancel):
        if cancel.is_set():
            raise AppError("ROBOT_CANCELLED", "데모 명령 취소")
        if not self.connected:
            raise AppError("ROBOT_DISCONNECTED", "데모 로봇이 연결되지 않았습니다.")
        self.session_id = command.session_id
        if command.operation == "configure":
            self.configuration = deepcopy(command.payload)
        elif command.operation == "start" and self.configuration is not None:
            self.running = True
            self.next_status = 0
        elif command.operation == "stop":
            self.running = False
        else:
            return RobotReply(command.session_id, command.sequence, False, "데모: 지원하지 않는 명령")
        return RobotReply(command.session_id, command.sequence, True, f"데모: {command.operation} 확인")

    def receive(self):
        now = self.clock.monotonic()
        if not self.connected or not self.running or now < self.next_status:
            return None
        self.next_status = now + 0.2
        self.sequence += 1
        return RobotEvent(self.session_id, self.sequence, "running", self.clock.now_utc(),
                          {"demo": True, "configuration_applied": True})

    def disconnect(self, *, timeout):
        self.connected = self.running = False
