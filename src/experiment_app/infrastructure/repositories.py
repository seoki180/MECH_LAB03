from dataclasses import asdict, replace
import json
from pathlib import Path
from threading import RLock
from uuid import uuid4
from experiment_app.domain.test_definition import AppError, definition_from_dict


class LocalTestRepository:
    """Atomic catalog replacement. Call on the application I/O executor."""
    def __init__(self, path, initial_groups, initial_tests):
        self.path = Path(path)
        self.lock = RLock()
        self.fail_save = False
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if data["schema_version"] != 1:
                    raise ValueError("지원하지 않는 저장 버전")
                self._groups = data["groups"]
                self._tests = [definition_from_dict(d) for d in data["tests"]]
            except (OSError, ValueError, KeyError, TypeError) as error:
                raise AppError("STORAGE_FAILED", f"저장 파일을 읽을 수 없습니다: {error}") from error
        else:
            self._groups, self._tests = dict(initial_groups), list(initial_tests)

    def groups(self):
        with self.lock:
            return dict(self._groups)

    def list(self):
        with self.lock:
            return list(self._tests)

    def get(self, test_id):
        with self.lock:
            for definition in self._tests:
                if definition.id == test_id:
                    return definition
        raise AppError("NOT_FOUND", "시험을 찾을 수 없습니다.")

    def _commit(self, tests, groups):
        if self.fail_save:
            raise AppError("STORAGE_FAILED", "데모 저장 실패입니다. 장치 탭에서 정상 시나리오로 바꾼 뒤 재시도하세요.")
        temporary = self.path.with_suffix(".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps({"schema_version": 1, "groups": groups,
                                "tests": [asdict(t) for t in tests]}, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.path)
        except OSError as error:
            raise AppError("STORAGE_FAILED", f"저장 실패: {error}") from error
        self._tests, self._groups = tests, groups

    def save(self, definition, expected_revision):
        with self.lock:
            previous = next((d for d in self._tests if d.id == definition.id), None)
            if (previous.revision if previous else 0) != expected_revision:
                raise AppError("REVISION_CONFLICT", "다른 변경이 저장되었습니다. 초안을 보존한 뒤 다시 불러오세요.")
            if definition.group_id not in self._groups:
                raise AppError("NOT_FOUND", "시험목록을 찾을 수 없습니다.")
            saved = replace(definition, revision=expected_revision + 1)
            tests = [saved if d.id == saved.id else d for d in self._tests]
            if previous is None:
                tests.append(saved)
            self._commit(tests, dict(self._groups))
            return saved

    def delete(self, test_id):
        with self.lock:
            self._commit([d for d in self._tests if d.id != test_id], dict(self._groups))

    def add_group(self, name):
        with self.lock:
            group_id = uuid4().hex
            self._commit(list(self._tests), {**self._groups, group_id: name})
            return group_id

    def delete_group(self, group_id):
        with self.lock:
            self._commit([d for d in self._tests if d.group_id != group_id],
                         {k: v for k, v in self._groups.items() if k != group_id})

    def reorder(self, test_id, offset):
        with self.lock:
            selected = self.get(test_id)
            tests = list(self._tests)
            indices = [i for i, d in enumerate(tests) if d.group_id == selected.group_id]
            current = next(i for i, index in enumerate(indices) if tests[index].id == test_id)
            target = current + offset
            if 0 <= target < len(indices):
                a, b = indices[current], indices[target]
                tests[a], tests[b] = tests[b], tests[a]
                self._commit(tests, dict(self._groups))
