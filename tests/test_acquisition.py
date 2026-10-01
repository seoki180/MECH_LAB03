"""수집을 언제 끝내는지, 그리고 push 방식 소스가 갈아끼워지는지 검증.

확인 범위:
- 시작하면 중지할 때까지 계속 받는다(고정 길이로 끊기지 않는다).
- 재생 소스는 자료가 끝나면 스스로 끝나고, 그때만 COMPLETED다.
- 시간 상한에 걸려 끊긴 것을 '완료'라고 부르지 않는다.
- 표본이 안 오는 주기를 0이나 지어낸 값으로 채우지 않는다.
- WebSocket처럼 밀려 들어오는 소스를 SessionService 수정 없이 끼울 수 있다.
"""

import time

import pytest

from experiment_app.application.session_service import SessionService
from sample_tests import seeded_services as build_services
from experiment_app.domain.session import State
from experiment_app.domain.telemetry import GpsFix, SensorSample
from experiment_app.infrastructure.sources import SystemClock
from experiment_app.infrastructure.streaming import StreamingSensorSource


def wait(condition, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def run_until_running(sessions, definition):
    session = sessions.prepare(definition.id, definition.revision)
    assert sessions.start(session.session_id)
    assert wait(lambda: sessions.current.state == State.RUNNING)
    return session


# --- 상한 없이 계속 받는다 ---

def test_session_keeps_collecting_until_stopped(tmp_path):
    """예전에는 30초에 끊겼다. 이제 중지할 때까지 계속 돌아야 한다."""
    main, experiment = build_services(tmp_path)
    try:
        sessions = experiment.sessions
        assert sessions.duration is None, "실제 운용의 기본값은 상한 없음이다"
        session = run_until_running(sessions, main.service.repository.list()[0])

        # 표본이 꾸준히 늘어나는지 두 번 관찰한다. 한 번만 보면 멈춘 것을 구별하지 못한다.
        assert wait(lambda: sessions.current.elapsed > 0.3)
        first = sessions.current.elapsed
        assert wait(lambda: sessions.current.elapsed > first + 0.3)
        assert sessions.current.state == State.RUNNING, "스스로 끝나지 않아야 한다"

        sessions.stop(session.session_id)
        assert wait(sessions.is_idle)
        view = sessions.view()
        assert view.state == State.STOPPED
        assert view.end_reason == "사용자 중지"
    finally:
        main.dispose()


def test_time_limit_is_not_reported_as_completion(tmp_path):
    """상한을 걸어 끊은 것은 완료가 아니다. 사유에 상한임이 드러나야 한다."""
    main, experiment = build_services(tmp_path, duration=0.3)
    try:
        sessions = experiment.sessions
        run_until_running(sessions, main.service.repository.list()[0])
        assert wait(sessions.is_idle)
        view = sessions.view()
        assert view.state == State.STOPPED, "상한은 완료가 아니다"
        assert "상한" in view.end_reason and "0.3" in view.end_reason
    finally:
        main.dispose()


# --- push 방식 소스 ---

class FakeWebSocketSource(StreamingSensorSource):
    """WebSocket 소스를 대신하는 시험용 구현.

    실제 구현도 이와 같은 자리만 채운다. 수신 스레드가 submit()을 부르고, 세션은
    read()로 쌓인 것을 가져간다. SessionService는 무엇이 꽂혔는지 모른다.
    """

    def __init__(self, clock):
        super().__init__(capacity=100)
        self.clock = clock
        self.connect_calls = 0
        self.disconnect_calls = 0

    def connect(self):
        self.connect_calls += 1

    def disconnect(self):
        self.disconnect_calls += 1

    def push(self, session_id, value):
        """장비가 값을 보낸 것처럼 만든다."""
        self.submit(SensorSample(session_id, "A.0", 0, self.clock.now_utc(),
                                 self.clock.monotonic(), value, "km/h"))


class SilentGps:
    def read(self, session_id, elapsed, scenario):
        return None


def test_streaming_source_plugs_in_without_changing_the_session(tmp_path):
    """WebSocket 방식 소스를 끼워도 세션이 그대로 돌고, 연결 해제까지 된다."""
    main, experiment = build_services(tmp_path)
    try:
        clock = SystemClock()
        source = FakeWebSocketSource(clock)
        sessions = experiment.sessions
        sessions.set_sources((("A", "A.0", "속도", "km/h"),), source, SilentGps())

        session = run_until_running(sessions, main.service.repository.list()[0])
        assert source.connect_calls == 1, "prepare()에서 연결을 연다"

        # 아직 아무것도 안 보냈다. 값이 없는데 화면에 값이 생기면 안 된다.
        assert experiment.telemetry.snapshot().samples == ()

        source.push(session.session_id, 12.5)
        assert wait(lambda: experiment.telemetry.snapshot().samples != ())
        assert experiment.telemetry.snapshot().samples[0].value == 12.5

        source.push(session.session_id, 13.5)
        assert wait(lambda: experiment.telemetry.snapshot().samples[0].value == 13.5)

        # 보내지 않아도 세션은 죽지 않고 기다린다.
        assert sessions.current.state == State.RUNNING

        sessions.stop(session.session_id)
        assert wait(sessions.is_idle)
        assert sessions.view().state == State.STOPPED
        assert source.disconnect_calls == 1, "close()에서 연결을 닫는다"
    finally:
        main.dispose()


def test_streaming_source_reports_dropped_samples(tmp_path):
    """큐가 넘치면 버린 수를 센다. 조용히 삼키지 않는다."""
    source = FakeWebSocketSource(SystemClock())
    for index in range(source._queue.maxlen + 5):
        source.push("s", index)
    assert source.dropped == 5
    batch = source.read("s", 0, 1, "정상")
    assert len(batch) == source._queue.maxlen
    # 버려진 것은 가장 오래된 쪽이다. 최신 값이 남아야 화면이 현재를 보여준다.
    assert batch[-1].value == source._queue.maxlen + 4


def test_empty_read_is_normal_and_not_filled_in(tmp_path):
    """표본이 없으면 빈 tuple이다. 0이나 이전 값으로 채우지 않는다."""
    source = FakeWebSocketSource(SystemClock())
    assert source.read("s", 0, 1, "정상") == ()
    source.push("s", 7.0)
    assert [s.value for s in source.read("s", 0, 2, "정상")] == [7.0]
    # 한 번 가져간 표본이 다시 나오지 않는다.
    assert source.read("s", 0, 3, "정상") == ()


def test_samples_from_a_previous_session_are_not_mixed_in():
    """이전 세션에 남아 있던 표본이 새 세션 자료로 섞이지 않는다."""
    source = FakeWebSocketSource(SystemClock())
    source.push("old-session", 1.0)
    source.push("new-session", 2.0)
    assert [s.value for s in source.read("new-session", 0, 1, "정상")] == [2.0]


# --- 재생 소스는 여전히 스스로 끝난다 ---

class ShortReplay:
    """0.4초 뒤 자료가 끝나는 재생 소스."""

    continuous = False

    def __init__(self, clock):
        self.clock = clock
        self.prepared = False

    def prepare(self):
        self.prepared = True

    def exhausted(self, elapsed):
        return elapsed >= 0.4

    def read(self, session_id, elapsed, sequence, scenario):
        return (SensorSample(session_id, "A.0", sequence, self.clock.now_utc(),
                             self.clock.monotonic(), elapsed, "km/h"),)


def test_replay_source_completes_on_its_own(tmp_path):
    """끝이 있는 소스는 자료를 다 읽으면 COMPLETED로 끝난다."""
    main, experiment = build_services(tmp_path)
    try:
        sessions = experiment.sessions
        sessions.set_sources((("A", "A.0", "속도", "km/h"),), ShortReplay(SystemClock()), SilentGps())
        run_until_running(sessions, main.service.repository.list()[0])
        assert wait(sessions.is_idle)
        view = sessions.view()
        assert view.state == State.COMPLETED
        assert view.end_reason == "재생 자료 끝까지 수집 완료"
    finally:
        main.dispose()


def test_continuous_source_ignores_an_exhausted_method(tmp_path):
    """continuous=True면 exhausted()가 있어도 끝으로 보지 않는다."""
    main, experiment = build_services(tmp_path)
    try:
        replay = ShortReplay(SystemClock())
        replay.continuous = True  # 실시간 소스라고 선언
        sessions = experiment.sessions
        sessions.set_sources((("A", "A.0", "속도", "km/h"),), replay, SilentGps())
        session = run_until_running(sessions, main.service.repository.list()[0])
        # exhausted가 True를 돌려주는 시점을 넉넉히 지나도 계속 돌아야 한다.
        assert wait(lambda: sessions.current.elapsed > 0.8)
        assert sessions.current.state == State.RUNNING
        sessions.stop(session.session_id)
        assert wait(sessions.is_idle)
        assert sessions.view().state == State.STOPPED
    finally:
        main.dispose()


def test_source_close_failure_becomes_an_error(tmp_path):
    """연결 해제가 실패하면 조용히 넘기지 않고 오류로 남긴다."""
    main, experiment = build_services(tmp_path)
    try:
        class BadClose(FakeWebSocketSource):
            def disconnect(self):
                raise OSError("소켓 종료 실패")

        sessions = experiment.sessions
        sessions.set_sources((("A", "A.0", "속도", "km/h"),), BadClose(SystemClock()), SilentGps())
        session = run_until_running(sessions, main.service.repository.list()[0])
        sessions.stop(session.session_id)
        assert wait(sessions.is_idle)
        view = sessions.view()
        assert view.state == State.ERROR
        assert "소스 종료 실패" in view.end_reason
    finally:
        main.dispose()
