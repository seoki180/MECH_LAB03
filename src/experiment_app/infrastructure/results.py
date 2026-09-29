import json
from pathlib import Path


class LocalResultRepository:
    def __init__(self, directory):
        self.directory = Path(directory)

    def load(self):
        if not self.directory.exists():
            return []
        return sorted((json.loads(p.read_text(encoding="utf-8")) for p in self.directory.glob("*.summary.json")),
                      key=lambda r: r["end_time"], reverse=True)

    def save(self, summary):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f'{summary["session_id"]}.summary.json'
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
