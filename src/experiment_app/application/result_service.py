from dataclasses import asdict
from threading import RLock


class ResultService:
    def __init__(self, repository):
        self.repository = repository
        self.lock = RLock()
        self.items = repository.load()

    def add(self, session, recording_path):
        summary = {"schema_version": 1, **asdict(session), "recording_path": recording_path,
                   "completeness": "불완전" if session.state == "ERROR" else "정상"}
        with self.lock:
            self.items.insert(0, summary)
        self.repository.save(summary)

    def list(self):
        with self.lock:
            return list(self.items)

    def get(self, session_id):
        return next(item for item in self.list() if item["session_id"] == session_id)
