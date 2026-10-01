from dataclasses import dataclass, replace
from copy import deepcopy
from datetime import datetime, timezone
import math
from uuid import uuid4


# 새 시험의 실험 입력 데이터. 값이 없음을 None으로 둔다. 0은 '측정/설정된 0'과
# 구별되지 않으므로 빈 값 대신 쓰지 않는다.
EMPTY_EXPERIMENT_DATA = {
    "point_angle": {"Zero": [None, None], "Accel": [None, None], "Brake": [None, None]},
    "calibration_data": [None, None],
    "limit_point": {"Accel": None, "Brake": None},
    "zero_brake_angle": None,
}


def utc_now():
    """타임존이 있는 UTC 시각. 저장 형식의 생성일자에 쓴다."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def experiment_data_paths(data):
    return {
        **{f"data/point_angle/{name}/{index}": value
           for name, pair in data["point_angle"].items() for index, value in enumerate(pair)},
        **{f"data/calibration_data/{index}": value
           for index, value in enumerate(data["calibration_data"])},
        **{f"data/limit_point/{name}": value for name, value in data["limit_point"].items()},
        "data/zero_brake_angle": data["zero_brake_angle"],
    }


class AppError(Exception):
    def __init__(self, code, message, errors=None):
        super().__init__(message)
        self.code = code
        self.errors = errors or {}


@dataclass(frozen=True)
class FieldSchema:
    key: str
    label: str
    kind: str = "float"
    unit: str = ""
    precision: int = 2
    required: bool = True
    minimum: float | None = None
    maximum: float | None = None
    options: tuple[str, ...] = ()
    help: str = "데모 설정값 · 실제 장비 제한 아님"

    def parse(self, value):
        if not self.required and (value is None or value == ""):
            return None
        if self.kind == "str":
            result = str(value).strip()
            if self.required and not result:
                raise ValueError("필수 항목입니다.")
        elif self.kind == "bool":
            if type(value) is not bool:
                raise ValueError("참/거짓을 선택하세요.")
            result = value
        else:
            if isinstance(value, bool):
                raise ValueError("숫자를 입력하세요.")
            try:
                result = int(str(value)) if self.kind == "int" else float(value)
            except (TypeError, ValueError):
                raise ValueError("정수를 입력하세요." if self.kind == "int" else "숫자를 입력하세요.") from None
            if not math.isfinite(result):
                raise ValueError("유한한 숫자를 입력하세요.")
            if self.minimum is not None and result < self.minimum:
                raise ValueError(f"{self.minimum:g} 이상으로 입력하세요.")
            if self.maximum is not None and result > self.maximum:
                raise ValueError(f"{self.maximum:g} 이하로 입력하세요.")
        if self.options and result not in self.options:
            raise ValueError("목록에서 선택하세요.")
        return result


@dataclass(frozen=True)
class TestDefinition:
    """하나의 시험. 식별 정보와 실험 입력 데이터만 담는다.

    name과 group_id는 폴더 이름이 정하므로 파일의 값은 사람이 읽기 위한 사본이다.
    revision은 저장 충돌 검출용 저장소 상태값이고, created_utc는 최초 저장 시각을
    그대로 보존한다(이름을 바꾸거나 값을 고쳐도 변하지 않는다).
    """
    id: str
    group_id: str
    revision: int
    name: str
    created_utc: str = ""
    experiment_data: dict | None = None

    ar_trapezoidal_step: dict | None = None
    pf_straight_line: dict | None = None

    def fields(self):
        fields: dict[str, object] = {"name": self.name}
        fields.update(experiment_data_paths(self.experiment_data or EMPTY_EXPERIMENT_DATA))
        from .robot_settings import ROBOT_FIELDS
        for section, schemas in ROBOT_FIELDS.items():
            data = getattr(self, section) or {}
            fields.update((f"robot/{section}/{schema.key}", data.get(schema.key)) for schema in schemas)
        return fields

    def patched(self, changes):
        data = deepcopy(self.experiment_data or EMPTY_EXPERIMENT_DATA)
        for path, value in changes.items():
            if path.startswith("data/"):
                keys = path.split("/")[1:]
                target = data
                for key in keys[:-1]:
                    target = target[int(key)] if isinstance(target, list) else target[key]
                key = keys[-1]
                target[int(key) if isinstance(target, list) else key] = value
        robots = {section: deepcopy(getattr(self, section) or {})
                  for section in ("ar_trapezoidal_step", "pf_straight_line")}
        for path, value in changes.items():
            if path.startswith("robot/"):
                _, section, key = path.split("/")
                robots[section][key] = value
        return replace(self, name=changes.get("name", self.name), experiment_data=data,
                       **{key: value or None for key, value in robots.items()})


@dataclass(frozen=True)
class FieldPatch:
    test_id: str
    base_revision: int
    changes: dict[str, object]


# 저장 형식에서 빠진 옛 키. 읽을 때 조용히 버린다.
# spec_items/runs/type_id는 화면도 로봇 전송도 쓰지 않던 잔여 구조이고,
# advanced_values는 삭제된 메모 필드다.
LEGACY_KEYS = ("spec_items", "runs", "type_id", "advanced_values")


def definition_from_dict(data):
    """저장 문서를 정의로 바꾼다. 빠진 생성일자와 남아 있는 옛 키를 모두 받아들인다."""
    data = {k: v for k, v in data.items() if k not in LEGACY_KEYS}
    return TestDefinition(**{**data,
        "created_utc": data.get("created_utc") or "",
        "experiment_data": deepcopy(data.get("experiment_data") or EMPTY_EXPERIMENT_DATA)})


def new_definition(group_id="", name="새 시험", created_utc=None):
    """빈 시험 정의. 어떤 입력값도 미리 채우지 않는다.

    revision 0은 '아직 저장되지 않음'을 뜻한다. 사용자가 값을 넣지 않고 저장해도
    되며, 검증은 입력한 항목에만 적용된다.
    """
    return TestDefinition(uuid4().hex, group_id, 0, name,
                          utc_now() if created_utc is None else created_utc,
                          experiment_data=deepcopy(EMPTY_EXPERIMENT_DATA))
