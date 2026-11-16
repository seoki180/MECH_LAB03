"""수집을 언제 끝내는지, 그리고 push 방식 소스가 갈아끼워지는지 검증.

확인 범위:
- 시작하면 중지할 때까지 계속 받는다(고정 길이로 끊기지 않는다).
- 재생 소스는 자료가 끝나면 스스로 끝나고, 그때만 COMPLETED다.
- 시간 상한에 걸려 끊긴 것을 '완료'라고 부르지 않는다.
- 표본이 안 오는 주기를 0이나 지어낸 값으로 채우지 않는다.
- WebSocket처럼 밀려 들어오는 소스를 SessionService 수정 없이 끼울 수 있다.
"""

import json
import time

import pytest

from experiment_app.application import session_service as session_service_module
from experiment_app.application.session_service import SessionService
from sample_tests import seeded_services as build_services
from experiment_app.domain.session import State
from experiment_app.domain.telemetry import GpsFix, SensorSample
from experiment_app.domain.test_definition import AppError
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


# --- 시작 전 소스 확인 (preflight) ---

class CheckedSource(FakeWebSocketSource):
    """시작 전 확인을 요구하는 소스. 통과 여부를 바깥에서 바꿀 수 있다."""

    def __init__(self, clock, ready=False):
        super().__init__(clock)
        self.ready = ready
        self.checks = 0

    def preflight(self):
        self.checks += 1
        return (True, "") if self.ready else (False, "위치가 유효하지 않습니다")


def test_start_is_blocked_until_the_source_check_passes(tmp_path):
    """기준점을 등록할 수 없는 상태에서는 시작을 막고 사유를 보여준다."""
    main, experiment = build_services(tmp_path)
    try:
        sessions = experiment.sessions
        source = CheckedSource(SystemClock())
        sessions.set_sources(((("A", "A.0", "속도", "km/h"),)), source, SilentGps())
        definition = main.service.repository.list()[0]
        session = sessions.prepare(definition.id, definition.revision)
        assert wait(lambda: source.checks >= 1)
        ready, reason = sessions.start_ready()
        assert ready is False and "위치가 유효하지 않습니다" in reason
        with pytest.raises(AppError) as caught:
            sessions.start(session.session_id)
        assert caught.value.code == "SOURCE_NOT_READY"
        assert sessions.view().state == State.READY, "거부된 시작이 상태를 바꾸지 않는다"
    finally:
        main.dispose()


def test_source_check_is_repeated_while_waiting(tmp_path):
    """fix를 기다리는 동안 다시 확인해서, 창을 닫았다 열지 않아도 버튼이 켜진다."""
    main, experiment = build_services(tmp_path)
    try:
        sessions = experiment.sessions
        source = CheckedSource(SystemClock())
        sessions.set_sources(((("A", "A.0", "속도", "km/h"),)), source, SilentGps())
        sessions.source_recheck_interval = 0.05
        definition = main.service.repository.list()[0]
        sessions.prepare(definition.id, definition.revision)
        assert wait(lambda: sessions.start_ready()[0] is False)
        # 장치에 fix가 잡혔다. 아무것도 다시 열지 않는다.
        source.ready = True
        assert wait(lambda: sessions.start_ready()[0] is True, timeout=3), "스스로 다시 확인해 풀려야 한다"
    finally:
        main.dispose()


# --- 단절과 복구 ---

GAP_KINDS = {"단절", "복구", "단절 중 종료"}


def gap_records(path):
    """기록 파일에서 단절·복구 사건만 읽는다(표본이 아니라 channel_id가 없다)."""
    records = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("kind") in GAP_KINDS:
                records.append(record)
    return records


def test_a_dropout_inside_the_grace_period_resumes_the_same_session(tmp_path):
    """끊겼다 돌아오면 같은 세션이 그대로 이어진다.

    예전에는 5초만 끊겨도 세션이 끝났다. 실험 창에서 다시 시작할 방법이 없으므로
    그렇게 끝내면 되살릴 수 없다. 수신 스레드는 1초마다 재접속을 계속 시도하므로
    기다리는 쪽이 맞다.
    """
    main, experiment = build_services(tmp_path)
    try:
        clock = SystemClock()
        source = FakeWebSocketSource(clock)
        sessions = experiment.sessions
        sessions.set_sources(((("A", "A.0", "속도", "km/h"),)), source, SilentGps())
        sessions.dropout_grace = 3.0
        session = run_until_running(sessions, main.service.repository.list()[0])
        source.push(session.session_id, 10.0)
        assert wait(lambda: experiment.telemetry.snapshot().samples != ())

        # 보내지 않는 동안 단절로 기록된다(DROPOUT_AFTER는 0.4초로 줄여 시험한다).
        monkey = session_service_module.DROPOUT_AFTER
        session_service_module.DROPOUT_AFTER = 0.4
        try:
            assert wait(lambda: sessions.gap_started_at is not None, timeout=3)
            assert sessions.current.state == State.RUNNING, "기다리는 동안에도 살아 있다"
            source.push(session.session_id, 11.0)  # 장비가 돌아왔다
            assert wait(lambda: sessions.current.gaps == 1, timeout=3)
        finally:
            session_service_module.DROPOUT_AFTER = monkey

        assert sessions.current.state == State.RUNNING, "복구 후에도 같은 세션이다"
        assert sessions.current.gap_seconds > 0
        sessions.stop(session.session_id)
        assert wait(sessions.is_idle)
        view = sessions.view()
        assert view.state == State.STOPPED
        assert "수신 단절 1회" in view.end_reason, "빈 구간이 있었다는 사실을 사유에 남긴다"

        kinds = [record["kind"] for record in gap_records(experiment.sessions.recorder.path)]
        assert kinds == ["단절", "복구"], "기록만 보고도 빈 구간의 이유를 알 수 있어야 한다"
        summary = experiment.sessions.results.get(session.session_id)
        assert summary["completeness"] == "불완전", "빈 구간이 있으면 온전한 측정이 아니다"
        assert summary["gaps"] == 1
    finally:
        main.dispose()


def test_a_dropout_past_the_grace_period_ends_the_session(tmp_path):
    """허용 시간을 넘기면 기다리지 않고 끝낸다. 사유에 단절임이 드러난다."""
    main, experiment = build_services(tmp_path)
    try:
        sessions = experiment.sessions
        sessions.set_sources(((("A", "A.0", "속도", "km/h"),)),
                             FakeWebSocketSource(SystemClock()), SilentGps())
        sessions.dropout_grace = 0.5
        run_until_running(sessions, main.service.repository.list()[0])
        assert wait(sessions.is_idle, timeout=5)
        view = sessions.view()
        assert view.state == State.ERROR
        assert "수신 단절" in view.end_reason and "허용" in view.end_reason
    finally:
        main.dispose()


def test_an_evicted_subscription_ends_the_session_immediately(tmp_path):
    """4001은 재접속하지 않으므로 기다릴 이유가 없다. 바로 끝내고 사유를 밝힌다."""
    main, experiment = build_services(tmp_path)
    try:
        source = FakeWebSocketSource(SystemClock())
        source.evicted = False
        sessions = experiment.sessions
        sessions.set_sources(((("A", "A.0", "속도", "km/h"),)), source, SilentGps())
        sessions.dropout_grace = 60.0
        session = run_until_running(sessions, main.service.repository.list()[0])
        source.push(session.session_id, 10.0)
        assert wait(lambda: experiment.telemetry.snapshot().samples != ())
        source.evicted = True  # 다른 프로그램이 구독을 가져갔다
        assert wait(sessions.is_idle, timeout=5)
        view = sessions.view()
        assert view.state == State.ERROR
        assert "4001" in view.end_reason
    finally:
        main.dispose()


def test_window_close_releases_what_the_source_holds_on_the_device(tmp_path):
    """기준점처럼 장치에 잡아 둔 것은 **창을 닫을 때** 놓는다.

    세션이 끝날 때 놓으면 단절로 끊긴 뒤 다시 시작할 때 기준점이 옮겨간다.
    """
    main, experiment = build_services(tmp_path)
    try:
        released = []

        class HoldingSource(CheckedSource):
            def release(self):
                released.append(True)

        sessions = experiment.sessions
        source = HoldingSource(SystemClock(), ready=True)
        sessions.set_sources(((("A", "A.0", "속도", "km/h"),)), source, SilentGps())
        definition = main.service.repository.list()[0]
        session = sessions.prepare(definition.id, definition.revision)
        assert wait(lambda: sessions.start_ready()[0] is True)
        assert sessions.start(session.session_id)
        assert wait(lambda: sessions.current.state == State.RUNNING)
        sessions.stop(session.session_id)
        assert wait(sessions.is_idle)
        assert released == [], "세션이 끝나는 것으로는 놓지 않는다"
        sessions.release_ready()  # 실험 창을 닫는다
        assert released == [True]
    finally:
        main.dispose()


def test_window_close_is_not_blocked_by_a_release_failure(tmp_path):
    """창이 안 닫히면 사용자가 빠져나갈 길이 없다. 실패는 사유로만 남긴다."""
    main, experiment = build_services(tmp_path)
    try:
        class BadRelease(CheckedSource):
            def release(self):
                raise AppError("LAN_REF_UNAVAILABLE", "기준점을 지우지 못했습니다")

        sessions = experiment.sessions
        sessions.set_sources(((("A", "A.0", "속도", "km/h"),)),
                             BadRelease(SystemClock(), ready=True), SilentGps())
        definition = main.service.repository.list()[0]
        sessions.prepare(definition.id, definition.revision)
        sessions.release_ready()
        assert "기준점을 지우지 못했습니다" in sessions.source_reason
    finally:
        main.dispose()


def test_source_check_keeps_watching_without_flicker(tmp_path):
    """통과한 뒤에도 계속 지켜보되, 확인 중에 버튼이 깜빡이지 않는다.

    한 번 통과했다고 멈추면 차가 움직이기 시작한 것을 놓친다. 반대로 확인 중이라고
    직전 결과를 지우면 5초마다 시작 버튼이 꺼졌다 켜진다.
    """
    main, experiment = build_services(tmp_path)
    try:
        sessions = experiment.sessions
        source = CheckedSource(SystemClock(), ready=True)
        sessions.set_sources(((("A", "A.0", "속도", "km/h"),)), source, SilentGps())
        sessions.source_recheck_interval = 0.01
        definition = main.service.repository.list()[0]
        sessions.prepare(definition.id, definition.revision)
        assert wait(lambda: sessions.start_ready()[0] is True)
        checks = source.checks
        for _ in range(30):
            assert sessions.start_ready()[0] is True, "확인이 도는 동안에도 켜져 있어야 한다"
            time.sleep(0.01)
        assert source.checks > checks, "준비 상태에서는 계속 다시 확인한다"
        # 차가 움직이기 시작하면 스스로 막힌다.
        source.ready = False
        assert wait(lambda: sessions.start_ready()[0] is False, timeout=3)
    finally:
        main.dispose()
