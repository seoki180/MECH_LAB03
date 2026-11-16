from dataclasses import replace
from copy import deepcopy
from threading import Event, RLock, Thread
from uuid import uuid4
from experiment_app.domain.session import Session, ExecutionSnapshot, State, ACTIVE, TERMINAL
from experiment_app.domain.telemetry import StreamGap
from experiment_app.domain.test_definition import AppError
from .robot_service import RobotService

# 이 사유로 끝난 세션만 COMPLETED다. 나머지(사용자 중지, 시간 상한, 오류)는 끝까지
# 받지 못한 것이므로 STOPPED/ERROR로 남긴다.
COMPLETION_REASONS = frozenset({"재생 자료 끝까지 수집 완료"})

# 마지막 수신 후 이만큼 지나면 '단절'로 보고 기록에 남긴다. 화면의 '연결 끊김'
# 판정(domain/telemetry.effective_quality)과 같은 값을 쓴다.
DROPOUT_AFTER = 5.0

# 단절을 이만큼까지 기다린다. 수신 스레드가 1초마다 재접속을 시도하므로 그 안에
# 돌아오면 측정이 그대로 이어진다. **제안 기본값이다** — 얼마나 빈 구간까지 한 시험으로
# 볼지는 측정 유효성 판단이므로 운용에서 정한다(bootstrap/SessionService에서 교체).
DROPOUT_GRACE = 60.0


class SessionService:
    def __init__(self, tests, channels, sensors, gps, recorder_factory, telemetry, results, clock, duration=None, robot=None):
        self.tests, self.channels = tests, channels
        self.sensors, self.gps, self.recorder_factory = sensors, gps, recorder_factory
        self.telemetry, self.results, self.clock = telemetry, results, clock
        # 측정 시간 상한(초). None이면 상한 없이 중지할 때까지 받는다. 실장비·WebSocket
        # 운용의 기본값이며, 값을 주는 것은 시험이 아니라 테스트를 짧게 끝내기 위해서다.
        self.duration = duration
        self.lock = RLock()
        self.current = None
        self.worker = None
        self.stop_event = Event()
        # 데모 고장 주입 이름("정상", "센서 지연/단절" …). 시험시나리오(target.csv)와
        # 다른 것이므로 헷갈리지 않게 한다.
        self.scenario = "정상"
        self.recorder = None
        self.robot = robot
        # 단절 허용 시간(초). 이 안에 수신이 돌아오면 측정을 이어 간다.
        self.dropout_grace = DROPOUT_GRACE
        # 단절이 시작된 단조 시각. 끊긴 동안만 값이 있다.
        self.gap_started_at = None
        # --- 로봇 설정 전송(세팅) 상태 ---
        # 시험 세팅은 실험 창을 열 때(prepare) 한 번 보내고, 시작 신호는 사용자가
        # 시작을 누를 때 보낸다. 세팅 전송은 HTTP라 GUI 스레드에서 하면 화면이 멈추므로
        # 작업 스레드에서 돌리고, 끝날 때까지 시작을 막는다.
        self.configure_worker = None
        self.configure_event = Event()
        self.configure_error = None
        # --- 입력 소스 시작 전 확인 상태 ---
        # 소스에 preflight()가 있으면 실험 창을 열 때 한 번 물어본다(HI-EDGE LAN은
        # 기준점을 등록할 수 있는 상태인지 확인한다). 장치 상태를 바꾸지 않는 조회이며,
        # 실패하면 시작 버튼을 막고 사유를 보여준다.
        self.source_worker = None
        self.source_ok = False
        self.source_reason = "장치 상태를 확인하지 않았습니다."
        # 확인이 한 번이라도 끝났는가. 확인이 도는 동안 직전 결과를 유지하기 위해
        # '결과 없음'과 '실패'를 구별한다.
        self.source_checked = False
        # 마지막 확인 시각(단조). READY 상태에서는 이 간격으로 다시 물어본다 —
        # GPS fix는 몇 분 걸리기도 하는데, 그때마다 실험 창을 닫았다 열게 하면
        # 로봇 세팅까지 다시 나간다.
        self.source_checked_at = None
        self.source_recheck_interval = 5.0
        # 시작할 때 세팅 전송을 기다려 주는 상한(초). 로봇 요청 타임아웃(3초)보다
        # 넉넉해야 '아직 보내는 중'과 '로봇이 응답하지 않음'을 구별할 수 있다.
        self.configure_timeout = 10

    # ------------------------------------------------------- 로봇 세팅 전송

    @property
    def configuring(self):
        """세팅 전송이 아직 진행 중인가."""
        worker = self.configure_worker
        return worker is not None and worker.is_alive()

    def robot_ready(self):
        """시작 신호를 보낼 수 있는 상태인지. (가능, 사유)를 돌려준다.

        로봇이 없거나 세팅이 끝났으면 가능하다. 전송 중이거나 실패했으면 불가능하며
        사유를 그대로 돌려준다 — 시작 버튼이 왜 눌리지 않는지 화면이 설명해야 한다.
        """
        if self.robot is None:
            return True, ""
        if self.configuring:
            return False, "로봇에 시험 설정을 보내는 중입니다."
        if self.configure_error is not None:
            return False, f"{getattr(self.configure_error, 'code', 'ROBOT_FAILED')}: {self.configure_error}"
        if not self.robot.connected:
            return False, "로봇에 시험 설정이 전송되지 않았습니다. 실험 창을 닫고 다시 여세요."
        return True, ""

    def wait_for_robot(self, timeout=5):
        """세팅 전송이 끝날 때까지 기다린다. (가능, 사유)를 돌려준다.

        GUI는 이것을 쓰지 않는다 — tick이 start_ready()로 버튼만 켜고 끈다.
        화면을 멈추지 않기 위해서다. 기다려도 되는 호출부(테스트, 콘솔)만 쓴다.
        """
        worker = self.configure_worker
        if worker is not None:
            worker.join(timeout)
        return self.robot_ready()

    def _configure_robot(self, session):
        """실험 창을 열 때 로봇에 시험 세팅값과 시나리오를 보낸다.

        작업 스레드에서 돌린다. 실패는 여기서 올리지 않고 configure_error에 남긴다 —
        창을 여는 동작 자체를 막기보다, 창을 열어 두고 시작을 막으면서 사유를 보여주는
        편이 사용자가 다음에 할 일을 알기 쉽다.
        """
        if self.robot is None:
            return
        self.configure_event = Event()
        self.configure_error = None
        cancel = self.configure_event

        def work():
            try:
                self.robot.begin(session, cancel)
            except Exception as exception:
                self.configure_error = exception
                self.robot.update(state="오류", message=str(exception))

        self.configure_worker = Thread(target=work, name="mechlab-robot-configure", daemon=True)
        self.configure_worker.start()

    def _cancel_configure(self, timeout=3):
        """진행 중인 세팅 전송을 취소하고 기다린다. 준비 세션을 버릴 때 쓴다."""
        self.configure_event.set()
        worker = self.configure_worker
        if worker is not None and worker.is_alive():
            worker.join(timeout)
        self.configure_worker = None

    # ------------------------------------------------------- 입력 소스 확인

    def _check_sources(self):
        """소스가 시작할 수 있는 상태인지 작업 스레드에서 확인한다.

        HTTP를 쓰므로 GUI 스레드에서 부르면 화면이 멈춘다. 로봇 세팅 전송과 같은
        방식으로 창을 열 때 시작해 두고, 끝날 때까지 시작 버튼을 막는다.

        **결과는 확인이 끝났을 때만 바꾼다.** 확인 중이라고 직전 결과를 지우면 5초마다
        시작 버튼이 깜빡인다.
        """
        check = getattr(self.sensors, "preflight", None)
        if check is None:
            self.source_ok, self.source_reason = True, ""
            self.source_checked = True
            self.source_worker = None
            return

        def work():
            try:
                ok, reason = check()
            except Exception as exception:
                ok = False
                reason = f"{getattr(exception, 'code', 'SOURCE_FAILED')}: {exception}"
            self.source_ok, self.source_reason = ok, reason
            self.source_checked = True
            self.source_checked_at = self.clock.monotonic()

        self.source_checked_at = self.clock.monotonic()
        self.source_worker = Thread(target=work, name="mechlab-source-preflight", daemon=True)
        self.source_worker.start()

    def source_ready(self):
        """소스 확인이 끝났고 통과했는가. (가능, 사유)를 돌려준다.

        준비 상태에서는 일정 간격으로 **계속 다시 확인한다.** GPS fix를 기다리는 동안
        화면이 스스로 풀려야 하고, 반대로 한 번 통과한 뒤 차가 움직였다면 그것도
        드러나야 한다. 확인은 장치 상태를 바꾸지 않고 구독 슬롯도 쓰지 않는 HTTP
        조회라 반복해도 부작용이 없다(장치 대시보드도 같은 간격으로 조회한다).

        확인이 도는 동안에는 **직전 결과를 유지한다.** 그래야 버튼이 깜빡이지 않는다.
        """
        if getattr(self.sensors, "preflight", None) is None:
            return True, ""
        worker = self.source_worker
        running = worker is not None and worker.is_alive()
        if not running and self._recheck_due():
            self._check_sources()
            running = True
        if not self.source_checked:
            return False, "장치 상태를 확인하는 중입니다." if running else self.source_reason
        return self.source_ok, self.source_reason

    def _recheck_due(self):
        """준비 상태에서 간격이 지났는가. 실행 중에는 다시 확인하지 않는다."""
        with self.lock:
            if not self.current or self.current.state != State.READY:
                return False
        last = self.source_checked_at
        return last is None or (self.clock.monotonic() - last) >= self.source_recheck_interval

    def start_ready(self):
        """시작 버튼을 켤 수 있는가. 로봇과 입력 소스를 모두 본다."""
        ready, reason = self.robot_ready()
        if not ready:
            return ready, reason
        return self.source_ready()

    def view(self):
        with self.lock:
            return self.current

    def prepare(self, test_id, revision, replace_ready=False):
        with self.lock:
            if self.worker and self.worker.is_alive():
                raise AppError("INVALID_STATE", "기존 세션의 정리가 끝나지 않았습니다.")
            if self.current and self.current.state not in TERMINAL:
                if self.current.snapshot.definition.id == test_id:
                    return self.current
                if self.current.state in ACTIVE or not replace_ready:
                    raise AppError("INVALID_STATE", "기존 실험을 종료하거나 준비 세션 교체를 확인하세요.")
            definition = self.tests.get(test_id)
            if definition.revision != revision:
                raise AppError("REVISION_CONFLICT", "최신 저장 버전으로 다시 준비하세요.")
            # 시나리오는 있으면 실행에 고정하고, 없으면 없는 대로 진행한다. 형식이
            # 어긋난 파일은 _scenario()가 읽는 단계에서 거부한다 — 깨진 파일을 '없음'과
            # 같이 취급하면 사용자가 의도한 목표값 없이 조용히 시험이 돌아간다.
            scenario = self._scenario(test_id)
            self.current = Session(uuid4().hex, ExecutionSnapshot(
                deepcopy(definition), self.channels, scenario or ())).transition(State.READY)
            self.telemetry.reset(self.current.session_id)
            session = self.current
        # 교체된 준비 세션의 전송이 남아 있으면 먼저 끊는다. 락 밖에서 기다린다.
        self._cancel_configure()
        # 여기서 로봇에 시험 세팅값과 target.csv를 보낸다(시작 신호는 아직이다).
        self._configure_robot(session)
        # 입력 소스도 지금 확인한다. HI-EDGE LAN은 시작할 때 현재 위치를 기준점으로
        # 등록하므로, 등록할 수 없는 상태(위치 무효·주행 중·자리 부족)를 시작을 누르기
        # 전에 알려야 한다.
        self._check_sources()
        return session

    def _scenario(self, test_id):
        """저장소가 시나리오를 제공할 때만 읽는다. 없는 구현이면 None(검사 생략)."""
        reader = getattr(self.tests, "scenario", None)
        return tuple(reader(test_id)) if reader is not None else None

    def release_ready(self):
        """실험 창을 닫을 때의 정리. 준비만 하고 끝난 세션도, 끝난 세션도 지난다.

        입력 소스가 장치에 잡아 둔 것(HI-EDGE는 기준점)도 여기서 놓는다. 세션이 끝날
        때가 아니라 **창을 닫을 때**인 이유는, 단절로 세션이 끊겨도 같은 기준점으로
        다시 시작할 수 있어야 하기 때문이다.
        """
        with self.lock:
            drop = bool(self.current and self.current.state == State.READY)
            if drop:
                self.current = None
        if drop:
            # 창을 닫으면 전송 중이던 세팅도 끊는다. 락 밖에서 기다린다.
            self._cancel_configure()
            # 세팅까지 간 연결은 닫아 준다. 시작 신호는 가지 않았다.
            if self.robot is not None and self.robot.connected:
                try:
                    self.robot.close()
                except Exception:
                    # 창을 닫는 길은 로봇 오류로 막지 않는다. 사유는 상태에 남는다.
                    pass
        self._release_sources()

    def _release_sources(self):
        """소스가 장치에 남긴 것을 치운다. 창을 닫는 길을 막지 않는다.

        실패해도 예외를 올리지 않는다 — 창이 닫히지 않으면 사용자가 빠져나갈 길이
        없다. 대신 사유를 소스에 남기고, 남은 기준점은 다음 등록 때 정리된다.
        """
        release = getattr(self.sensors, "release", None)
        if release is None:
            return
        try:
            release()
        except Exception as exception:
            self.source_reason = f"{getattr(exception, 'code', 'SOURCE_RELEASE_FAILED')}: {exception}"

    def start(self, session_id):
        # 세팅 전송이 아직이면 잠깐 기다린다. 락 밖에서 기다려야 전송 스레드가
        # 끝날 수 있다. GUI는 전송 중에 시작 버튼을 막으므로 여기서 멈추지 않는다.
        if self.configuring:
            worker = self.configure_worker
            if worker is not None:
                worker.join(self.configure_timeout)
        with self.lock:
            if not self.current or self.current.session_id != session_id or self.current.state != State.READY:
                return False
            # 세팅이 끝나지 않았거나 실패했으면 시작 신호를 보내지 않는다.
            # 로봇이 설정을 받지 못한 채 구동하면 의도와 다른 시험이 된다.
            ready, reason = self.robot_ready()
            if not ready:
                raise AppError("ROBOT_NOT_READY", reason)
            # 입력 소스도 마찬가지다. 기준점을 등록할 수 없는 상태로 시작하면 변위와
            # 이동거리가 빈 채로 기록된다.
            ready, reason = self.source_ready()
            if not ready:
                raise AppError("SOURCE_NOT_READY", reason)
            self.stop_event = Event()
            self.current = self.current.transition(State.STARTING, cleaned_up=False)
            self.worker = Thread(target=self._run, args=(self.scenario,), name="mechlab-acquisition", daemon=True)
            self.worker.start()
            return True

    def set_sources(self, channels, sensors, gps):
        with self.lock:
            if self.worker and self.worker.is_alive() or self.current and self.current.state not in TERMINAL:
                raise AppError("INVALID_STATE", "실험 창을 종료한 뒤 파일을 선택하세요.")
            self.channels = channels
            self.sensors, self.gps = sensors, gps
            # 소스가 바뀌면 이전 확인 결과는 의미가 없다. 다음 prepare에서 다시 본다.
            self.source_worker = None
            self.source_ok = False
            self.source_reason = "장치 상태를 확인하지 않았습니다."
            self.source_checked = False
            self.source_checked_at = None

    def set_robot(self, transport):
        """로봇 전송을 바꾼다. 실행 중에는 거부한다.

        전송만 받아 RobotService로 감싼다. 호출부가 서비스를 만들게 하면 clock이나
        타임아웃을 서로 다르게 주는 경로가 생긴다.

        set_sources와 같은 제약을 쓴다. 준비된 세션이 이미 이전 로봇으로 설정을
        보냈을 수 있으므로, 실행 중 교체를 허용하면 어느 로봇이 구동하는지 알 수 없다.
        """
        with self.lock:
            if self.worker and self.worker.is_alive() or self.current and self.current.state not in TERMINAL:
                raise AppError("INVALID_STATE", "실험 창을 종료한 뒤 로봇 주소를 바꾸세요.")
            self.robot = RobotService(transport, self.clock) if transport is not None else None

    def _watch_stream(self, elapsed, recorder):
        """필수 채널(A)의 수신을 지켜본다. 단절을 기록하고, 허용 시간을 넘기면 끝낸다.

        예전에는 5초만 끊겨도 세션을 끝냈다. 그런데 수신 스레드는 1초마다 재접속을
        계속 시도하고 기준점도 창을 닫을 때까지 살아 있으므로, **기다리면 그대로
        재개된다.** 끝내 버리면 되살릴 방법이 없다(실험 창에서 다시 시작할 수 없다).

        그렇다고 조용히 기다리기만 하면 자료가 비어 있는 구간이 생긴 채 온전한 시험처럼
        보인다. 그래서 단절과 복구를 기록에 남기고 세션에 횟수·시간을 누적한다.
        """
        # 4001은 재접속하지 않는다. 기다려도 돌아오지 않으므로 바로 끝낸다.
        if getattr(self.sensors, "evicted", False):
            raise AppError("DEVICE_UNAVAILABLE",
                           "다른 프로그램이 장치 구독을 가져갔습니다(4001). 자동 재접속하지 않습니다.")
        now = self.clock.monotonic()
        latest = {sample.channel_id: sample for sample in self.telemetry.snapshot().samples}
        age = 0.0
        for part, channel, _, _ in self.channels:
            if part != "A":
                continue
            sample = latest.get(channel)
            age = max(age, now - sample.monotonic_received if sample else elapsed)
        if age >= self.dropout_grace:
            raise AppError("DEVICE_UNAVAILABLE",
                           f"수신 단절 {age:.0f}초 · 허용 {self.dropout_grace:g}초를 넘겨 중지")
        if age >= DROPOUT_AFTER and self.gap_started_at is None:
            self.gap_started_at = now - age
            self._record_gap(recorder, "단절", elapsed, None,
                             f"마지막 수신 후 {age:.1f}초")
        elif age < DROPOUT_AFTER and self.gap_started_at is not None:
            duration = now - self.gap_started_at
            self.gap_started_at = None
            self._record_gap(recorder, "복구", elapsed, duration,
                             f"{duration:.1f}초 만에 수신 재개")
            with self.lock:
                self.current = replace(self.current, gaps=self.current.gaps + 1,
                                       gap_seconds=self.current.gap_seconds + duration)

    def _record_gap(self, recorder, kind, elapsed, duration, detail):
        """단절·복구를 기록에 남긴다. 기록 실패가 측정을 끝내지는 않는다."""
        event = StreamGap(self.current.session_id, kind, self.clock.now_utc(),
                          self.clock.monotonic(), elapsed, duration, detail)
        recorder.append((event,))

    def _close_open_gap(self, recorder, begun):
        """끊긴 채로 측정이 끝났으면 그 사실을 닫아 준다.

        복구되지 못한 단절도 횟수·시간에 들어가야 한다. 그러지 않으면 마지막 빈 구간이
        결과에 드러나지 않는다. 기록이 이미 깨진 경우에는 세션 집계만 남긴다.
        """
        started = self.gap_started_at
        if started is None:
            return
        duration = self.clock.monotonic() - started
        self.gap_started_at = None
        with self.lock:
            self.current = replace(self.current, gaps=self.current.gaps + 1,
                                   gap_seconds=self.current.gap_seconds + duration)
            elapsed = self.current.elapsed
        if not begun:
            return
        try:
            self._record_gap(recorder, "단절 중 종료", elapsed, duration,
                             f"{duration:.1f}초 동안 수신이 돌아오지 않았습니다")
        except Exception:
            # 기록이 이미 실패한 상태일 수 있다. 종료 경로를 여기서 막지 않는다.
            pass

    def _reached_end(self, elapsed, continuous, exhausted):
        """수집을 끝낼 때인지 판단하고, 끝내는 경우 사실에 맞는 사유를 남긴다.

        사용자 중지는 이 함수가 아니라 stop_event가 처리한다. 여기서 끝나는 경우는
        재생 자료가 떨어졌거나(재생 소스), 시험과 무관하게 걸어 둔 시간 상한에
        걸렸을 때뿐이다. 상한 때문에 끊긴 것을 '완료'라고 부르지 않는다.
        """
        if exhausted is not None and not continuous and exhausted(elapsed):
            self.stop(self.current.session_id, "재생 자료 끝까지 수집 완료")
            return True
        if self.duration is not None and elapsed >= self.duration:
            self.stop(self.current.session_id, f"시간 상한 {self.duration:g}초에 도달해 중지")
            return True
        return False

    def stop(self, session_id, reason="사용자 중지"):
        with self.lock:
            if not self.current or self.current.session_id != session_id or self.current.state not in ACTIVE:
                return False
            if self.current.state != State.STOPPING:
                self.current = self.current.transition(State.STOPPING, end_reason=reason)
            self.stop_event.set()
            return True

    def _run(self, scenario):
        recorder = self.recorder_factory()
        self.recorder = recorder
        self.gap_started_at = None
        begun = False
        error = None
        recording_path = ""
        start = self.clock.monotonic()
        try:
            if hasattr(self.sensors, "prepare"):
                self.sensors.prepare()
            recorder.begin(self.view(), scenario)
            begun = True
            if self.robot:
                # 세팅값과 시나리오는 실험 창을 열 때(prepare) 이미 보냈다.
                # 여기서는 시작 신호만 보낸다.
                self.robot.start(self.stop_event)
            start = self.clock.monotonic()
            with self.lock:
                if self.current.state == State.STARTING:
                    self.current = self.current.transition(State.RUNNING, start_time=self.clock.now_utc())
            sequence, next_gps = 0, 0
            # 재생 소스는 자료가 끝나면 멈춘다. 실시간 소스(실장비·WebSocket)는 끝이
            # 없으므로 사용자가 중지할 때까지 계속 받는다.
            continuous = getattr(self.sensors, "continuous", False)
            exhausted = getattr(self.sensors, "exhausted", None)
            while not self.stop_event.is_set():
                elapsed = self.clock.monotonic() - start
                if sequence and self._reached_end(elapsed, continuous, exhausted):
                    break
                sequence += 1
                session_id = self.current.session_id
                samples = self.sensors.read(session_id, elapsed, sequence, scenario)
                fix = None
                if elapsed >= next_gps:
                    fix = self.gps.read(session_id, elapsed, scenario)
                    next_gps = elapsed + 1
                robot_event = self.robot.receive() if self.robot else None
                # push 소스는 도착한 것이 없을 수 있다. 빈 주기를 기록하지 않는다.
                batch = (*samples, *((fix,) if fix else ()),
                         *((robot_event,) if robot_event else ()))
                if batch:
                    recorder.append(batch)
                if samples or fix:
                    self.telemetry.ingest(samples, fix)
                with self.lock:
                    self.current = replace(self.current, elapsed=elapsed)
                self._watch_stream(elapsed, recorder)
                self.stop_event.wait(0.02)
        except Exception as exception:
            if not (getattr(exception, "code", "") == "ROBOT_CANCELLED" and self.stop_event.is_set()):
                error = exception
        finally:
            self.telemetry.freeze_quality(self.clock.monotonic())
            self._close_open_gap(recorder, begun)
            with self.lock:
                if self.current.state in {State.STARTING, State.RUNNING}:
                    self.current = self.current.transition(State.STOPPING)
            if self.robot:
                try:
                    self.robot.close()
                except Exception as exception:
                    error = exception if error is None else AppError(
                        "ROBOT_CLEANUP_FAILED", f"{error}; 로봇 종료 실패: {exception}")
                if error:
                    self.robot.update(state="오류", message=str(error))
            # 소스 연결 해제. WebSocket 소스는 여기서 소켓을 닫는다. 정리 실패가
            # 측정 결과를 덮어쓰지 않도록 이미 있는 오류를 우선한다.
            if hasattr(self.sensors, "close"):
                try:
                    self.sensors.close()
                except Exception as exception:
                    if error is None:
                        error = AppError("SOURCE_CLEANUP_FAILED", f"소스 종료 실패: {exception}")
            if begun:
                recording_path = str(recorder.path)
                try:
                    recorder.finalize(self.current)
                except Exception as exception:
                    error = exception
            with self.lock:
                reason = f"{getattr(error, 'code', 'START_FAILED')}: {error}" if error else self.current.end_reason
                state = State.ERROR if error else (State.COMPLETED if reason in COMPLETION_REASONS else State.STOPPED)
                # 자료가 빈 구간이 있었다는 사실은 사유에도 남긴다. 결과 목록에서
                # 끝난 이유만 보고 온전한 시험으로 오해하지 않게 한다.
                if self.current.gaps:
                    reason = (f"{reason} · 수신 단절 {self.current.gaps}회"
                              f" 합계 {self.current.gap_seconds:.1f}초")
                self.current = self.current.transition(state, end_time=self.clock.now_utc(), end_reason=reason,
                                                       elapsed=self.clock.monotonic() - start,
                                                       cleaned_up=not (begun and recorder.worker.is_alive()))
            try:
                self.results.add(self.current, recording_path)
            except Exception as exception:
                with self.lock:
                    self.current = replace(self.current, state=State.ERROR, end_reason=f"결과 요약 저장 실패: {exception}")
                # The in-memory partial result must also reflect persistence failure.
                with self.results.lock:
                    if self.results.items and self.results.items[0]["session_id"] == self.current.session_id:
                        self.results.items[0].update(state=State.ERROR, completeness="불완전", end_reason=self.current.end_reason)
            finally:
                with self.lock:
                    alive = begun and recorder.worker.is_alive()
                    self.current = replace(self.current, cleaned_up=not alive)

    def is_idle(self):
        return (self.worker is None or not self.worker.is_alive()) and (not self.current or self.current.cleaned_up)

    def retry_cleanup(self):
        with self.lock:
            if not self.current or self.current.cleaned_up or (self.worker and self.worker.is_alive()):
                return False
            def cleanup():
                try:
                    self.recorder.finalize(self.current)
                except Exception as error:
                    with self.lock:
                        self.current = replace(self.current, end_reason=f"{getattr(error, 'code', 'STORAGE_FAILED')}: {error}")
                finally:
                    with self.lock:
                        self.current = replace(self.current, cleaned_up=not self.recorder.worker.is_alive())
            self.worker = Thread(target=cleanup, name="mechlab-cleanup", daemon=True)
            self.worker.start()
            return True
