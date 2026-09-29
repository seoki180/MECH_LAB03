from dataclasses import replace
from copy import deepcopy
from threading import Event, RLock, Thread
from uuid import uuid4
from experiment_app.domain.session import Session, ExecutionSnapshot, State, ACTIVE, TERMINAL
from experiment_app.domain.test_definition import AppError


class SessionService:
    def __init__(self, tests, channels, sensors, gps, recorder_factory, telemetry, results, clock, duration=30, robot=None):
        self.tests, self.channels = tests, channels
        self.sensors, self.gps, self.recorder_factory = sensors, gps, recorder_factory
        self.telemetry, self.results, self.clock, self.duration = telemetry, results, clock, duration
        self.lock = RLock()
        self.current = None
        self.worker = None
        self.stop_event = Event()
        self.scenario = "정상"
        self.recorder = None
        self.robot = robot

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
            self.current = Session(uuid4().hex, ExecutionSnapshot(deepcopy(definition), self.channels)).transition(State.READY)
            self.telemetry.reset(self.current.session_id)
            return self.current

    def release_ready(self):
        with self.lock:
            if self.current and self.current.state == State.READY:
                self.current = None

    def start(self, session_id):
        with self.lock:
            if not self.current or self.current.session_id != session_id or self.current.state != State.READY:
                return False
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
                self.robot.begin(self.view(), self.stop_event)
                self.robot.start(self.stop_event)
            start = self.clock.monotonic()
            with self.lock:
                if self.current.state == State.STARTING:
                    self.current = self.current.transition(State.RUNNING, start_time=self.clock.now_utc())
            sequence, next_gps = 0, 0
            while not self.stop_event.is_set():
                elapsed = self.clock.monotonic() - start
                source_duration = getattr(self.sensors, "duration", None)
                limit = min(self.duration, source_duration) if source_duration is not None else self.duration
                if sequence and elapsed >= limit:
                    self.stop(self.current.session_id, "NMEA 재생 완료" if source_duration is not None else "데모 30초 자동 완료")
                    break
                sequence += 1
                session_id = self.current.session_id
                samples = self.sensors.read(session_id, elapsed, sequence, scenario)
                fix = None
                if elapsed >= next_gps:
                    fix = self.gps.read(session_id, elapsed, scenario)
                    next_gps = elapsed + 1
                robot_event = self.robot.receive() if self.robot else None
                recorder.append((*samples, *((fix,) if fix else ()),
                                 *((robot_event,) if robot_event else ())))
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
            if begun:
                recording_path = str(recorder.path)
                try:
                    recorder.finalize(self.current)
                except Exception as exception:
                    error = exception
            with self.lock:
                reason = f"{getattr(error, 'code', 'START_FAILED')}: {error}" if error else self.current.end_reason
                state = State.ERROR if error else (State.COMPLETED if reason in {"데모 30초 자동 완료", "NMEA 재생 완료"} else State.STOPPED)
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
