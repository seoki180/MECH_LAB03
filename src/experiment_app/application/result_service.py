from dataclasses import asdict
from threading import RLock


class ResultService:
    def __init__(self, repository):
        self.repository = repository
        self.lock = RLock()
        self.items = repository.load()

    def add(self, session, recording_path):
        # 자료가 빈 구간(수신 단절)이 있었으면 온전한 측정이 아니다. 끝난 상태와
        # 별개로 표시해야, 정상 종료된 시험의 그래프를 그대로 믿지 않게 된다.
        incomplete = session.state == "ERROR" or bool(getattr(session, "gaps", 0))
        summary = {"schema_version": 1, **asdict(session), "recording_path": recording_path,
                   "completeness": "불완전" if incomplete else "정상"}
        with self.lock:
            self.items.insert(0, summary)
        self.repository.save(summary)

    def list(self):
        with self.lock:
            return list(self.items)

    def get(self, session_id):
        return next(item for item in self.list() if item["session_id"] == session_id)
