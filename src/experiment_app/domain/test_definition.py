from dataclasses import dataclass, replace
from copy import deepcopy
import math


DEFAULT_EXPERIMENT_DATA = {
    "point_angle": {"Zero": [0, -19], "Accel": [22, -8.3], "Brake": [-9.5, -16.6]},
    "calibration_data": [0.02894060757546641, -13.496798692559123],
    "limit_point": {"Accel": -0.2, "Brake": -5},
    "zero_brake_angle": -14,
}


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
class SpecItem:
    id: str
    name: str
    values: tuple[tuple[str, object], ...]
    schema_id: str = "demo-step"
    schema_version: int = 1


@dataclass(frozen=True)
class TestDefinition:
    id: str
    group_id: str
    revision: int
    type_id: str
    name: str
    runs: int
    spec_items: tuple[SpecItem, ...]
    experiment_data: dict | None = None

    ar_trapezoidal_step: dict | None = None
    pf_straight_line: dict | None = None

    def fields(self):
        fields = {"name": self.name, "runs": self.runs, "type_id": self.type_id}
        for item in self.spec_items:
            fields.update((f"spec/{item.id}/{k}", v) for k, v in item.values)
        fields.update(experiment_data_paths(self.experiment_data or DEFAULT_EXPERIMENT_DATA))
        from .robot_settings import ROBOT_FIELDS
        for section, schemas in ROBOT_FIELDS.items():
            data = getattr(self, section) or {}
            fields.update((f"robot/{section}/{schema.key}", data.get(schema.key)) for schema in schemas)
        return fields

    def patched(self, changes):
        specs = tuple(replace(s, values=tuple((k, changes.get(f"spec/{s.id}/{k}", v))
                      for k, v in s.values)) for s in self.spec_items)
        data = deepcopy(self.experiment_data or DEFAULT_EXPERIMENT_DATA)
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
        return replace(self, name=changes.get("name", self.name), runs=changes.get("runs", self.runs),
                       spec_items=specs, experiment_data=data,
                       **{key: value or None for key, value in robots.items()})


@dataclass(frozen=True)
class FieldPatch:
    test_id: str
    base_revision: int
    changes: dict[str, object]


def definition_from_dict(data):
    # advanced_values held the removed 메모 field; drop it so older save files still load.
    data = {k: v for k, v in data.items() if k != "advanced_values"}
    return TestDefinition(**{**data,
        "spec_items": tuple(SpecItem(**{**s, "values": tuple(tuple(v) for v in s["values"])})
                            for s in data["spec_items"]),
        "experiment_data": deepcopy(data.get("experiment_data") or DEFAULT_EXPERIMENT_DATA)})
