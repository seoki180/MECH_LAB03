"""시험을 실행 파일 밖 ``test/`` 폴더에서 직접 관리하는 저장소.

배치::

    MECHLab/
      MECHLab.exe
      test/
        10km 시험/                ← 시험목록
          급가속 A/               ← 시험 하나 = profile 폴더
            test.json             ← 로봇에 한 번 보내는 시험 설정
            target.csv            ← 시험시나리오(time,target_v)
          급제동 B/
            test.json
            target.csv

시험 하나가 폴더 하나다. 두 자료가 한 시험을 이루므로 경계를 폴더로 긋는다.
폴더째 복사·이동·압축하면 시험이 통째로 따라가고, 반쪽만 옮겨지는 경로가 없다.
폴더 이름이 표시 이름이고 안쪽 파일 이름은 ``test.json``/``target.csv`` 로 고정한다.
파일 이름이 짝을 짓는 역할을 하지 않으므로 이름을 바꿔도 짝이 깨지지 않는다.

사용자가 탐색기에서 폴더를 직접 만지는 것을 전제로 한다. 앱 내부 저장 형식을
따로 두지 않고 이 트리를 그대로 읽고 쓴다.

모든 호출은 application의 I/O 실행기에서 이루어진다(GUI 스레드 아님).
"""
from dataclasses import asdict, replace
import json
import re
import shutil
from pathlib import Path
from threading import RLock
from uuid import uuid4

from experiment_app.domain.scenario import (SCENARIO_SUFFIX, format_scenario_csv,
                                            parse_scenario_csv)
from experiment_app.domain.test_definition import AppError, definition_from_dict

SCHEMA_VERSION = 3
# profile 폴더 안에서 고정으로 쓰는 이름. 바깥 폴더 이름만 시험 이름을 따른다.
TEST_FILE = "test.json"
SCENARIO_FILE = "target" + SCENARIO_SUFFIX
# Windows 파일 이름 금지 문자. 폴더를 그대로 주고받는 것이 목적이므로 가장 좁은
# 규칙에 맞춘다.
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


def safe_name(name, fallback="이름없음"):
    """표시 이름을 파일/폴더 이름으로 바꾼다. 내용을 바꾸지 않고 금지 문자만 없앤다."""
    cleaned = _FORBIDDEN.sub("_", str(name)).strip().rstrip(".")
    if not cleaned or cleaned in {".", ".."} or cleaned.upper().split(".")[0] in _RESERVED:
        return fallback
    return cleaned[:120]


def is_profile(folder):
    """시험 profile 폴더인지. ``test.json``이 있으면 시험으로 본다."""
    return (folder / TEST_FILE).is_file()


class FolderTestRepository:
    """``test/<시험목록>/<시험>/test.json`` 트리를 그대로 읽고 쓰는 저장소."""

    def __init__(self, root, legacy_path=None):
        self.root = Path(root)
        self.lock = RLock()
        self.fail_save = False
        self.load_errors = []
        self._groups, self._tests, self._folders, self._order = {}, [], {}, {}
        self._migrate_flat()
        self._migrate_legacy_file(legacy_path)
        self.reload()

    # ------------------------------------------------------------------ 적재

    def _migrate_flat(self):
        """예전 배치(시험목록 폴더에 ``이름.json``/``이름.csv``)를 profile 폴더로 옮긴다.

        같은 이름 짝으로 관리하던 때의 자료를 한 번만 정리한다. 이미 profile
        폴더로 되어 있으면 아무것도 하지 않는다.
        """
        if not self.root.is_dir():
            return
        try:
            groups = [c for c in self.root.iterdir() if c.is_dir() and not c.name.startswith(".")]
        except OSError:
            return
        for group in groups:
            try:
                loose = sorted(group.glob("*.json"))
            except OSError:
                continue
            for document in loose:
                folder = self._unique_folder(group, document.stem)
                try:
                    folder.mkdir(parents=True)
                    document.replace(folder / TEST_FILE)
                    scenario = document.with_suffix(SCENARIO_SUFFIX)
                    if scenario.is_file():
                        scenario.replace(folder / SCENARIO_FILE)
                except OSError as error:
                    raise AppError("STORAGE_FAILED",
                                   f"예전 시험 자료를 폴더로 옮기지 못했습니다: {error}") from error

    def _migrate_legacy_file(self, legacy_path):
        """예전 단일 파일(.mechlab/tests.json)이 있고 폴더가 비어 있을 때만 한 번 옮긴다.

        데모 자료를 만들어 넣지는 않는다. 시험이 없는 빈 상태는 정상이며, 사용자가
        시험목록과 시험을 추가하거나 폴더를 가져와서 채운다.
        """
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            if any(child.is_dir() and not child.name.startswith(".") for child in self.root.iterdir()):
                return
        except OSError as error:
            raise AppError("STORAGE_FAILED", f"시험 폴더를 열 수 없습니다: {error}") from error
        legacy_path = Path(legacy_path) if legacy_path else None
        if not legacy_path or not legacy_path.is_file():
            return
        groups, tests = self._read_legacy(legacy_path)
        for group_id, name in groups.items():
            group_folder = self.root / safe_name(name or group_id, group_id)
            group_folder.mkdir(parents=True, exist_ok=True)
            for order, definition in enumerate(t for t in tests if t.group_id == group_id):
                definition = replace(definition, group_id=group_folder.name)
                folder = self._unique_folder(group_folder, definition.name)
                self._write_document(folder, definition, order)

    @staticmethod
    def _read_legacy(path):
        """예전 단일 파일(.mechlab/tests.json)을 폴더 구조로 옮기기 위해 읽는다."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return dict(data["groups"]), [definition_from_dict(d) for d in data["tests"]]
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise AppError("STORAGE_FAILED", f"이전 저장 파일을 읽을 수 없습니다: {error}") from error

    def reload(self):
        """폴더를 다시 훑는다. 앱 밖에서 파일을 바꾼 뒤 호출한다."""
        with self.lock:
            groups, tests, folders, order = {}, [], {}, {}
            errors = []
            seen = set()
            try:
                group_folders = sorted((c for c in self.root.iterdir() if c.is_dir()
                                        and not c.name.startswith(".")), key=lambda c: c.name)
            except OSError as error:
                raise AppError("STORAGE_FAILED", f"시험 폴더를 읽을 수 없습니다: {error}") from error
            for group_folder in group_folders:
                groups[group_folder.name] = group_folder.name
                loaded = []
                try:
                    children = sorted((c for c in group_folder.iterdir()
                                       if c.is_dir() and not c.name.startswith(".")),
                                      key=lambda c: c.name)
                except OSError as error:
                    errors.append((group_folder, AppError(
                        "STORAGE_FAILED", f"{group_folder.name}을(를) 읽을 수 없습니다: {error}")))
                    continue
                for folder in children:
                    if not is_profile(folder):
                        # test.json이 없는 폴더는 시험이 아니다. 사용자가 둔 자료일 수
                        # 있으므로 오류로 보고하지 않고 지나간다.
                        continue
                    try:
                        definition, index = self._read_document(folder, group_folder.name)
                    except AppError as error:
                        errors.append((folder / TEST_FILE, error))
                        continue
                    if definition.id in seen:
                        # 같은 id가 두 폴더에 있으면 나중 폴더에 새 id를 준다.
                        # 복사해 붙여 넣은 시험을 조용히 덮어쓰지 않기 위해서다.
                        definition = replace(definition, id=uuid4().hex)
                        self._write_document(folder, definition, index)
                    seen.add(definition.id)
                    loaded.append((index, folder.name, definition, folder))
                for position, (_, _, definition, folder) in enumerate(
                        sorted(loaded, key=lambda item: (item[0], item[1]))):
                    tests.append(definition)
                    folders[definition.id] = folder
                    order[definition.id] = position
            self._groups, self._tests, self._folders, self._order = groups, tests, folders, order
            self.load_errors = errors
            return errors

    @staticmethod
    def _read_document(folder, group_name):
        """profile 폴더의 ``test.json``을 읽는다. 표시 이름은 폴더 이름이 정한다."""
        path = folder / TEST_FILE
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise AppError("STORAGE_FAILED", f"{folder.name}을(를) 읽을 수 없습니다: {error}") from error
        if not isinstance(data, dict):
            raise AppError("STORAGE_FAILED", f"{folder.name}: test.json의 최상위 형식이 객체가 아닙니다.")
        version = data.pop("schema_version", SCHEMA_VERSION)
        if version not in (1, 2, SCHEMA_VERSION):
            raise AppError("STORAGE_FAILED", f"{folder.name}: 지원하지 않는 저장 버전 {version}")
        index = data.pop("order", 1 << 30)
        # 손으로 만든 파일에는 앱 내부 식별자가 없을 수 있다. 구조 키는 채우고,
        # 실제 시험 내용(단계)은 채우지 않는다.
        data.setdefault("id", uuid4().hex)
        data.setdefault("revision", 1)
        data.setdefault("type_id", "demo")
        data.setdefault("runs", 1)
        data["group_id"] = group_name
        # 폴더 이름이 곧 시험 이름이다. 탐색기에서 폴더 이름을 바꾸면 그대로 반영된다.
        data["name"] = folder.name
        if "spec_items" not in data:
            raise AppError("STORAGE_FAILED", f"{folder.name}: test.json에 spec_items가 없습니다.")
        try:
            definition = definition_from_dict(data)
        except (TypeError, KeyError, ValueError) as error:
            raise AppError("STORAGE_FAILED", f"{folder.name}의 내용을 해석할 수 없습니다: {error}") from error
        return definition, index if isinstance(index, int) else 1 << 30

    # ------------------------------------------------------------------ 쓰기

    def _write_document(self, folder, definition, order):
        if self.fail_save:
            raise AppError("STORAGE_FAILED", "데모 저장 실패입니다. 장치 탭에서 정상 시나리오로 바꾼 뒤 재시도하세요.")
        body = {"schema_version": SCHEMA_VERSION, "order": order, **asdict(definition)}
        path = folder / TEST_FILE
        temporary = folder / (TEST_FILE + ".tmp")
        try:
            folder.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
        except OSError as error:
            raise AppError("STORAGE_FAILED", f"저장 실패: {error}") from error

    def _unique_folder(self, group_folder, name, keep=None):
        """표시 이름에서 겹치지 않는 profile 폴더 경로를 만든다. keep은 자기 자신."""
        base = safe_name(name)
        candidate = group_folder / base
        index = 2
        while candidate.exists() and candidate != keep:
            candidate = group_folder / f"{base} ({index})"
            index += 1
        return candidate

    # ------------------------------------------------------------------ 조회

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

    def folder_of(self, test_id):
        """시험 profile 폴더. 시험을 이루는 모든 자료가 이 안에 있다."""
        with self.lock:
            folder = self._folders.get(test_id)
        if folder is None:
            raise AppError("NOT_FOUND", "시험 폴더를 찾을 수 없습니다.")
        return folder

    def path_of(self, test_id):
        return self.folder_of(test_id) / TEST_FILE

    def scenario_path(self, test_id):
        return self.folder_of(test_id) / SCENARIO_FILE

    def scenario(self, test_id):
        """시험시나리오. 파일이 없으면 빈 열을 돌려준다(실행은 세션에서 막는다)."""
        path = self.scenario_path(test_id)
        if not path.is_file():
            return ()
        try:
            body = path.read_text(encoding="utf-8-sig")
        except OSError as error:
            raise AppError("STORAGE_FAILED", f"시나리오를 읽을 수 없습니다: {error}") from error
        return parse_scenario_csv(body, f"{path.parent.name}/{path.name}")

    def set_scenario(self, test_id, source):
        """CSV 파일을 이 시험의 시나리오로 삼는다. 형식 검사 후 복사한다."""
        source = Path(source)
        try:
            body = source.read_text(encoding="utf-8-sig")
        except OSError as error:
            raise AppError("STORAGE_FAILED", f"시나리오 파일을 읽을 수 없습니다: {error}") from error
        points = parse_scenario_csv(body, source.name)
        target = self.scenario_path(test_id)
        try:
            target.write_text(format_scenario_csv(points), encoding="utf-8", newline="")
        except OSError as error:
            raise AppError("STORAGE_FAILED", f"시나리오 저장 실패: {error}") from error
        return points

    def clear_scenario(self, test_id):
        self.scenario_path(test_id).unlink(missing_ok=True)

    # ------------------------------------------------------------------ 변경

    def save(self, definition, expected_revision):
        with self.lock:
            previous = next((d for d in self._tests if d.id == definition.id), None)
            if (previous.revision if previous else 0) != expected_revision:
                raise AppError("REVISION_CONFLICT", "다른 변경이 저장되었습니다. 초안을 보존한 뒤 다시 불러오세요.")
            if definition.group_id not in self._groups:
                raise AppError("NOT_FOUND", "시험목록을 찾을 수 없습니다.")
            group_folder = self.root / definition.group_id
            current = self._folders.get(definition.id)
            saved = replace(definition, revision=expected_revision + 1)
            order = self._order.get(definition.id, len(
                [d for d in self._tests if d.group_id == definition.group_id]))
            target = self._unique_folder(group_folder, saved.name, keep=current)
            if target.name != saved.name:
                # 금지 문자나 이름 충돌로 폴더 이름이 달라지면 표시 이름도 맞춘다.
                # 폴더 이름이 곧 시험 이름이므로 둘이 어긋나게 두지 않는다.
                saved = replace(saved, name=target.name)
            # 저장이 실패할 수 있으므로 지금 폴더에 먼저 쓴다. 폴더를 먼저 옮기면
            # 쓰기가 실패했을 때 이름만 바뀐 채로 남는다.
            self._write_document(current or target, saved, order)
            if current is not None and current != target:
                # 이름이 바뀌면 폴더째 옮긴다. 안쪽 자료는 이름이 고정이라 그대로 따라간다.
                try:
                    current.replace(target)
                except OSError as error:
                    raise AppError("STORAGE_FAILED", f"시험 폴더 이름을 바꿀 수 없습니다: {error}") from error
            self.reload()
            return self.get(saved.id)

    def delete(self, test_id):
        with self.lock:
            folder = self.folder_of(test_id)
            try:
                shutil.rmtree(folder)
            except OSError as error:
                raise AppError("STORAGE_FAILED", f"시험 폴더를 지울 수 없습니다: {error}") from error
            self.reload()

    def add_group(self, name):
        with self.lock:
            folder = self.root / safe_name(name)
            if folder.exists():
                raise AppError("VALIDATION_FAILED", "같은 이름의 시험목록이 이미 있습니다.")
            try:
                folder.mkdir(parents=True)
            except OSError as error:
                raise AppError("STORAGE_FAILED", f"시험목록 폴더를 만들 수 없습니다: {error}") from error
            self.reload()
            return folder.name

    def delete_group(self, group_id):
        with self.lock:
            group_folder = self.root / group_id
            if not group_folder.is_dir():
                raise AppError("NOT_FOUND", "시험목록 폴더를 찾을 수 없습니다.")
            for child in list(group_folder.iterdir()):
                if child.is_dir() and is_profile(child):
                    shutil.rmtree(child, ignore_errors=True)
            try:
                group_folder.rmdir()
            except OSError:
                # 앱이 만들지 않은 자료가 남아 있으면 시험목록 폴더를 지우지 않는다.
                pass
            self.reload()

    def reorder(self, test_id, offset):
        with self.lock:
            selected = self.get(test_id)
            siblings = [d for d in self._tests if d.group_id == selected.group_id]
            current = next(i for i, d in enumerate(siblings) if d.id == test_id)
            target = current + offset
            if not 0 <= target < len(siblings):
                return
            siblings[current], siblings[target] = siblings[target], siblings[current]
            for position, definition in enumerate(siblings):
                self._write_document(self._folders[definition.id], definition, position)
            self.reload()

    # ------------------------------------------------------ 가져오기/내보내기

    def export(self, test_id, destination):
        """시험 profile 폴더를 통째로 지정한 위치에 복사한다."""
        destination = Path(destination)
        source = self.folder_of(test_id)
        try:
            destination.mkdir(parents=True, exist_ok=True)
            target = self._unique_folder(destination, source.name)
            shutil.copytree(source, target)
        except OSError as error:
            raise AppError("STORAGE_FAILED", f"내보내기 실패: {error}") from error
        return target

    def import_test(self, source, group_id):
        """외부 시험 폴더를 시험목록으로 통째로 들여온다."""
        source = Path(source)
        with self.lock:
            if group_id not in self._groups:
                raise AppError("NOT_FOUND", "가져올 시험목록을 선택하세요.")
            if not source.is_dir():
                raise AppError("VALIDATION_FAILED", "시험 폴더를 선택하세요.")
            if not is_profile(source):
                raise AppError("VALIDATION_FAILED",
                               f"'{source.name}' 안에 {TEST_FILE}이(가) 없습니다. 시험 폴더가 아닙니다.")
            group_folder = self.root / group_id
            # 내용을 먼저 검사한다. 읽지 못할 시험을 폴더에 들여놓지 않는다.
            definition, _ = self._read_document(source, group_id)
            scenario = source / SCENARIO_FILE
            if scenario.is_file():
                parse_scenario_csv(scenario.read_text(encoding="utf-8-sig"),
                                   f"{source.name}/{SCENARIO_FILE}")
            target = self._unique_folder(group_folder, definition.name)
            try:
                shutil.copytree(source, target)
            except OSError as error:
                raise AppError("STORAGE_FAILED", f"가져오기 실패: {error}") from error
            if any(d.id == definition.id for d in self._tests):
                definition = replace(definition, id=uuid4().hex)
            definition = replace(definition, name=target.name, group_id=group_id)
            order = len([d for d in self._tests if d.group_id == group_id])
            self._write_document(target, definition, order)
            self.reload()
            return self.get(definition.id)
