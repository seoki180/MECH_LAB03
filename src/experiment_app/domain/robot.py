"""Device-independent command and receive contracts (not a wire protocol)."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RobotCommand:
    session_id: str
    sequence: int
    operation: str
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RobotReply:
    session_id: str
    sequence: int
    accepted: bool
    message: str = ""


@dataclass(frozen=True)
class RobotEvent:
    session_id: str
    sequence: int
    state: str
    received_time_utc: str
    data: dict = field(default_factory=dict)
    kind: str = "robot"


@dataclass(frozen=True)
class RobotView:
    session_id: str = ""
    state: str = "미연결"
    message: str = ""
    last_received_utc: str = ""
    demo: bool = False
