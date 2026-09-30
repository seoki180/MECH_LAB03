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
    """측정 표본 공급자. 세션 작업 스레드에서만 호출한다.

    구현은 두 가지다. ``continuous``가 True이면 사람이 중지할 때까지 계속 받는
    실시간 소스(실장비, WebSocket)이고, False이면 끝이 있는 재생 소스(.nmea 파일)다.
    세션은 이 값으로 정지 조건을 정한다.

    ``read``는 **표본이 없을 때 빈 tuple을 돌려줄 수 있다**. push 방식 소스
    (WebSocket)는 도착한 것이 없을 수 있기 때문이다. 세션은 빈 결과를 정상으로
    보고 다음 주기를 기다린다. 표본이 오지 않는 상태가 이어지면 단절로 판정하는 것은
    세션의 watchdog이 하며, 소스가 스스로 값을 지어내지 않는다.

    ``elapsed``는 세션 경과 시간(초, 단조 시계)이다. 재생 소스는 이것으로 파일 위치를
    찾고, 실시간 소스는 보통 무시한다. 실시간 소스의 표본 시각은 수신 시각이다.

    선택 구현:
    - ``prepare()``   세션 시작 직전 1회. 파일 적재나 연결 수립에 쓴다.
    - ``exhausted(elapsed)``  재생 소스가 자료를 다 내보냈는지. 없으면 끝이 없다고 본다.
    - ``close()``     세션 종료 시 1회. 연결 해제에 쓴다. 예외를 밖으로 던지지 않는다.
    """
    continuous: bool

    def read(self, session_id, elapsed, sequence, scenario): ...


class GpsSource(Protocol):
    """GPS 공급자. 계약은 SensorSource와 같고, fix가 없으면 None을 돌려준다."""

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
