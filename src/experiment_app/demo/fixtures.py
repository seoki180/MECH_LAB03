from copy import deepcopy
from experiment_app.domain.test_definition import FieldSchema, SpecItem, TestDefinition, DEFAULT_EXPERIMENT_DATA

STEP_FIELDS = (
    FieldSchema("target", "목표값", unit="demo unit"),
    FieldSchema("duration", "단계 시간", unit="s", minimum=0),
    FieldSchema("enabled", "사용", kind="bool"),
)
BASIC_FIELDS = (FieldSchema("name", "시험 이름", "str", help="저장할 실험 설정 이름"),)
DATA_FIELDS = {
    "point_angle": tuple(FieldSchema(f"{name}/{index}", f"{name} · 값 {index + 1}", precision=8,
                                     help="실험 입력 데이터 · 각도 값")
                         for name in ("Zero", "Accel", "Brake") for index in range(2)),
    "calibration_data": tuple(FieldSchema(str(index), f"보정값 {index + 1}", precision=16,
                                          help="실험 입력 데이터 · 보정값") for index in range(2)),
    "limit_point": tuple(FieldSchema(name, name, help="실험 입력 데이터 · 한계값")
                         for name in ("Accel", "Brake")),
    "zero_brake_angle": (FieldSchema("zero_brake_angle", "값",
                                     help="실험 입력 데이터 · 각도 값"),),
}
# (part, channel ID, label, unit): deliberately not physical device definitions.
CHANNELS = tuple((part, f"{part}.{i}", f"Demo {part} · {i + 1}", "demo unit")
                 for part in "ABC" for i in range(2))
SCENARIOS = ("정상", "센서 지연/단절", "값 오류", "GPS fix 손실", "지도 배경 실패", "기록 실패", "저장 실패")


def fixtures():
    groups = {"group-a": "시험목록 A · 데모", "group-b": "시험목록 B · 데모"}
    definitions = []
    for index in range(6):
        specs = tuple(SpecItem(f"step-{index}-{i}", f"단계 {i + 1}",
                     (("target", 10.0 * (i + 1)), ("duration", 10.0), ("enabled", True))) for i in range(3))
        definitions.append(TestDefinition(f"demo-{index}", "group-a" if index < 3 else "group-b",
                           1, "demo", f"데모 시험 {index + 1:02}", 1, specs,
                           experiment_data=deepcopy(DEFAULT_EXPERIMENT_DATA)))
    return groups, definitions


def schemas(definition):
    fields = {s.key: s for s in BASIC_FIELDS}
    for item in definition.spec_items:
        fields.update((f"spec/{item.id}/{s.key}", s) for s in STEP_FIELDS)
    for section, section_fields in DATA_FIELDS.items():
        prefix = "data/" if section == "zero_brake_angle" else f"data/{section}/"
        fields.update((prefix + field.key, field) for field in section_fields)
    from experiment_app.domain.robot_settings import ROBOT_FIELDS
    for section, robot_fields in ROBOT_FIELDS.items():
        fields.update((f"robot/{section}/{field.key}", field) for field in robot_fields)
    return fields
