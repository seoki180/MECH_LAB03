from dataclasses import asdict
import json
from pathlib import Path
from queue import Queue, Empty, Full
from threading import Event, Thread
from experiment_app.domain.test_definition import AppError


class JsonlRecorder:
    """Bounded raw queue, lossless within capacity; overflow is a session error."""
    def __init__(self, directory):
        self.directory = Path(directory)

    def begin(self, session, scenario):
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / f"{session.session_id}.jsonl"
        self.file = self.path.open("w", encoding="utf-8")
        try:
            self.file.write(json.dumps({"schema_version": 1, "snapshot": asdict(session.snapshot)}, ensure_ascii=False) + "\n")
        except Exception:
            self.file.close()
            raise
        self.queue = Queue(maxsize=1000)
        self.done = Event()
        self.error = None
        self.count = 0
        self.scenario = scenario
        self.worker = Thread(target=self._write, name="mechlab-recorder", daemon=True)
        self.worker.start()

    def _write(self):
        try:
            while not self.done.is_set() or not self.queue.empty():
                try:
                    batch = self.queue.get(timeout=0.05)
                except Empty:
                    continue
                if self.scenario == "기록 실패" and self.count >= 100:
                    raise OSError("데모 기록 오류")
                for sample in batch:
                    self.file.write(json.dumps(asdict(sample), ensure_ascii=False) + "\n")
                self.count += 1
            self.file.flush()
        except Exception as error:
            self.error = AppError("STORAGE_FAILED", f"기록 실패: {error}")
        finally:
            self.file.close()

    def append(self, batch):
        if self.error:
            raise self.error
        try:
            self.queue.put_nowait(batch)
        except Full:
            raise AppError("RECORDING_OVERFLOW", "원본 기록 버퍼가 가득 찼습니다.") from None

    def finalize(self, session):
        self.done.set()
        self.worker.join(timeout=3)
        if self.worker.is_alive():
            raise AppError("STOP_TIMEOUT", "기록 종료가 지연됩니다.")
        if self.error:
            raise self.error
        return str(self.path)
