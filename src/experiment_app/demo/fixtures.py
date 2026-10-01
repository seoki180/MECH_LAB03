"""화면이 쓰는 입력 스키마와, 실장비 없이 쓰는 데모 센서/시나리오 목록.

시험 자료는 여기서 만들지 않는다. 앱은 빈 시험 폴더로 시작하며 시험은 사용자가
추가하거나 폴더를 가져와서 생긴다. 데모 측정값(센서 채널)과 실패 시나리오만 남긴다.

입력 스키마의 값 항목은 모두 **미설정 허용**이다. 값을 넣으면 그때 자료형·범위를
검사하고(검증 규칙은 바뀌지 않는다), 비워 두면 '값 없음'으로 저장한다. 0을 빈 값
대신 쓰지 않는다 — 0은 실제로 설정된 0과 구별되지 않는다.
"""
from experiment_app.domain.test_definition import FieldSchema

# 값 항목의 공통 설명. 실제 장비 제한은 미확정이다.
_VALUE_HELP = "미설정 허용 · 장비 제한 미확정"


def value_field(key, label, **kwargs):
    """미설정을 허용하는 값 항목. required만 다르고 검증 규칙은 그대로다."""
    kwargs.setdefault("help", _VALUE_HELP)
    return FieldSchema(key, label, required=False, **kwargs)


STEP_FIELDS = (
    value_field("target", "목표값"),
    value_field("duration", "단계 시간", unit="s", minimum=0),
    value_field("enabled", "사용", kind="bool"),
)
# 시험 이름은 폴더 이름이므로 비울 수 없다.
BASIC_FIELDS = (FieldSchema("name", "시험 이름", "str", help="저장할 실험 설정 이름"),)
DATA_FIELDS = {
    "point_angle": tuple(value_field(f"{name}/{index}", f"{name} · 값 {index + 1}", precision=8,
                                     help="실험 입력 데이터 · 각도 값 · 미설정 허용")
                         for name in ("Zero", "Accel", "Brake") for index in range(2)),
    "calibration_data": tuple(value_field(str(index), f"보정값 {index + 1}", precision=16,
                                          help="실험 입력 데이터 · 보정값 · 미설정 허용")
                              for index in range(2)),
    "limit_point": tuple(value_field(name, name, help="실험 입력 데이터 · 한계값 · 미설정 허용")
                         for name in ("Accel", "Brake")),
    "zero_brake_angle": (value_field("zero_brake_angle", "값",
                                     help="실험 입력 데이터 · 각도 값 · 미설정 허용"),),
}
# (part, channel ID, label, unit): deliberately not physical device definitions.
CHANNELS = tuple((part, f"{part}.{i}", f"Demo {part} · {i + 1}", "demo unit")
                 for part in "ABC" for i in range(2))
SCENARIOS = ("정상", "센서 지연/단절", "값 오류", "GPS fix 손실", "지도 배경 실패", "기록 실패", "저장 실패")


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
