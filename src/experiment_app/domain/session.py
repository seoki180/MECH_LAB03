from dataclasses import dataclass, replace
from enum import StrEnum
from .test_definition import TestDefinition, AppError


class State(StrEnum):
    PREPARING = "PREPARING"
    READY = "READY"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


ACTIVE = {State.STARTING, State.RUNNING, State.STOPPING}
TERMINAL = {State.COMPLETED, State.STOPPED, State.ERROR}
TRANSITIONS = {
    State.PREPARING: {State.READY, State.ERROR},
    State.READY: {State.STARTING},
    State.STARTING: {State.RUNNING, State.STOPPING, State.ERROR},
    State.RUNNING: {State.STOPPING},
    State.STOPPING: TERMINAL,
}
LABELS = {State.PREPARING: "준비 중", State.READY: "준비됨", State.STARTING: "시작 중",
          State.RUNNING: "측정 중", State.STOPPING: "기록 정리 중", State.COMPLETED: "완료",
          State.STOPPED: "사용자 중지", State.ERROR: "오류 · 불완전"}


@dataclass(frozen=True)
class ExecutionSnapshot:
    definition: TestDefinition
    channels: tuple[tuple[str, str, str, str], ...]


@dataclass(frozen=True)
class Session:
    session_id: str
    snapshot: ExecutionSnapshot
    state: State = State.PREPARING
    start_time: str = ""
    end_time: str = ""
    end_reason: str = ""
    elapsed: float = 0
    cleaned_up: bool = True

    def transition(self, state, **updates):
        if state not in TRANSITIONS.get(self.state, set()):
            raise AppError("INVALID_STATE", f"{self.state} → {state} 전환 불가")
        return replace(self, state=state, **updates)
