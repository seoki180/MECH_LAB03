from typing import Protocol
from threading import Event
from experiment_app.domain.robot import RobotCommand, RobotReply, RobotEvent
from experiment_app.domain.test_definition import TestDefinition


class TestRepository(Protocol):
    def get(self, test_id: str) -> TestDefinition: ...
    def list(self) -> list[TestDefinition]: ...
    def groups(self) -> dict[str, str]: ...
    def save(self, definition: TestDefinition, expected_revision: int) -> TestDefinition: ...
    def delete(self, test_id: str) -> None: ...
    def reorder(self, test_id: str, offset: int) -> None: ...
    def add_group(self, name: str) -> str: ...
    def delete_group(self, group_id: str) -> None: ...


class Clock(Protocol):
    def now_utc(self) -> str: ...
    def monotonic(self) -> float: ...


class SensorSource(Protocol):
    def read(self, session_id, elapsed, sequence, scenario): ...


class GpsSource(Protocol):
    def read(self, session_id, elapsed, scenario): ...


class Recorder(Protocol):
    def begin(self, session, scenario): ...
    def append(self, batch): ...
    def finalize(self, session): ...


class ClipboardPort(Protocol):
    def set_text(self, text: str) -> bool: ...


class MapTileSource(Protocol):
    """오프라인 지도 배경. XYZ 규약(원점 좌상단)의 타일 바이트를 돌려준다.

    구현체만 지도 공급자를 안다. 타일이 없으면 None이며 지도 없이도 좌표·궤적은 표시한다.
    """
    available: bool
    min_zoom: int
    max_zoom: int
    attribution: str

    def tile(self, zoom: int, x: int, y: int) -> bytes | None: ...


class RobotTransport(Protocol):
    """All calls run on the session worker. No wx dependencies.

    connect/exchange must honor the timeout (monotonic seconds) and cancellation.
    receive is nonblocking, returns at most one RobotEvent or None. Adapters bound
    their incoming queue and report overflow/disconnection as AppError; they must
    never silently discard recording data. disconnect is bounded by timeout.
    A reply acknowledges its command's session_id and sequence. Only configure
    success after device-side application/validation may return accepted=True.
    Map these semantic contracts to the actual SDK/wire protocol in infrastructure.
    """
    demo: bool

    def connect(self, *, timeout: float, cancel: Event) -> None: ...
    def exchange(self, command: RobotCommand, *, timeout: float, cancel: Event) -> RobotReply: ...
    def receive(self) -> RobotEvent | None: ...
    def disconnect(self, *, timeout: float) -> None: ...
