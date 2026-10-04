from dataclasses import replace
from copy import deepcopy
from threading import Event, RLock, Thread
from uuid import uuid4
from experiment_app.domain.session import Session, ExecutionSnapshot, State, ACTIVE, TERMINAL
from experiment_app.domain.test_definition import AppError
from .robot_service import RobotService

# 이 사유로 끝난 세션만 COMPLETED다. 나머지(사용자 중지, 시간 상한, 오류)는 끝까지
# 받지 못한 것이므로 STOPPED/ERROR로 남긴다.
COMPLETION_REASONS = frozenset({"재생 자료 끝까지 수집 완료"})


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
        # --- 로봇 설정 전송(세팅) 상태 ---
        # 시험 세팅은 실험 창을 열 때(prepare) 한 번 보내고, 시작 신호는 사용자가
        # 시작을 누를 때 보낸다. 세팅 전송은 HTTP라 GUI 스레드에서 하면 화면이 멈추므로
        # 작업 스레드에서 돌리고, 끝날 때까지 시작을 막는다.
        self.configure_worker = None
        self.configure_event = Event()
        self.configure_error = None
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

        GUI는 이것을 쓰지 않는다 — tick이 robot_ready()로 버튼만 켜고 끈다.
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
        return session

    def _scenario(self, test_id):
        """저장소가 시나리오를 제공할 때만 읽는다. 없는 구현이면 None(검사 생략)."""
        reader = getattr(self.tests, "scenario", None)
        return tuple(reader(test_id)) if reader is not None else None

    def release_ready(self):
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
                # Demo A is required. Apply timeouts to actual reception, not scenario names.
                latest = {sample.channel_id: sample for sample in self.telemetry.snapshot().samples}
                for part, channel, label, unit in self.channels:
                    if part == "A":
                        sample = latest.get(channel)
                        age = self.clock.monotonic() - sample.monotonic_received if sample else elapsed
                        if age >= 5:
                            raise AppError("DEVICE_UNAVAILABLE", "필수 Demo A 연결 끊김 · 마지막 수신 후 5초")
                self.stop_event.wait(0.02)
        except Exception as exception:
            if not (getattr(exception, "code", "") == "ROBOT_CANCELLED" and self.stop_event.is_set()):
                error = exception
        finally:
            self.telemetry.freeze_quality(self.clock.monotonic())
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
