"""테스트에서 쓰는 시험 자료.

앱은 빈 시험 폴더로 시작한다(데모 시험을 만들어 넣지 않는다). 저장·검증·실행 경로를
확인하려면 자료가 필요하므로 테스트가 직접 폴더를 만들어 넣는다. 여기 있는 값은
테스트 입력일 뿐이며 실제 장비 설정이 아니다.
"""
from copy import deepcopy
import json

from experiment_app.domain.scenario import ScenarioPoint, format_scenario_csv
from experiment_app.domain.test_definition import TestDefinition
from experiment_app.infrastructure.test_folders import SCENARIO_FILE, SCHEMA_VERSION, TEST_FILE

# 예전 DEFAULT_EXPERIMENT_DATA. 값이 채워진 시험을 흉내내는 테스트 입력이다.
SAMPLE_EXPERIMENT_DATA = {
    "point_angle": {"Zero": [0, -19], "Accel": [22, -8.3], "Brake": [-9.5, -16.6]},
    "calibration_data": [0.02894060757546641, -13.496798692559123],
    "limit_point": {"Accel": -0.2, "Brake": -5},
    "zero_brake_angle": -14,
}
GROUPS = ("시험목록 A · 데모", "시험목록 B · 데모")
SAMPLE_CREATED = "2026-01-02T03:04:05+00:00"
# 테스트용 시험시나리오 길이와 간격.
SCENARIO_SECONDS = 30.0
SCENARIO_STEP = 0.1


def sample_scenario():
    """0에서 목표 속도까지 올린 뒤 유지하는 단순한 곡선. 실제 시험 프로파일이 아니다."""
    peak, ramp = 10.0, SCENARIO_SECONDS / 3
    steps = int(round(SCENARIO_SECONDS / SCENARIO_STEP)) + 1
    return tuple(ScenarioPoint(round(i * SCENARIO_STEP, 3),
                               round(peak * min(1.0, (i * SCENARIO_STEP) / ramp), 6))
                 for i in range(steps))


def sample_definitions():
    """demo-0 … demo-5. 앞 세 개가 첫 시험목록, 나머지가 두 번째다."""
    return [TestDefinition(f"demo-{index}", GROUPS[0 if index < 3 else 1], 1,
                           f"데모 시험 {index + 1:02}", SAMPLE_CREATED,
                           experiment_data=deepcopy(SAMPLE_EXPERIMENT_DATA))
            for index in range(6)]


def seeded_services(*args, **kwargs):
    """build_services + 기본 시험 자료. 빈 폴더로 시작하는 앱에 테스트 자료를 넣는다."""
    from experiment_app.bootstrap import build_services
    main, experiment = build_services(*args, **kwargs)
    seed(main.service.repository)
    return main, experiment


def document(definition, order):
    """저장소가 쓰는 그 형식의 test.json 본문."""
    return {"schema_version": SCHEMA_VERSION, "order": order,
            "id": definition.id, "name": definition.name,
            "created_utc": definition.created_utc, "revision": definition.revision,
            "experiment_data": definition.experiment_data,
            "ar_trapezoidal_step": definition.ar_trapezoidal_step,
            "pf_straight_line": definition.pf_straight_line}


def seed(repository, definitions=None, scenario=True):
    """시험 폴더 트리를 직접 만든다. 저장소가 읽는 그 형식으로 쓴다."""
    definitions = list(definitions if definitions is not None else sample_definitions())
    body = format_scenario_csv(sample_scenario()) if scenario else None
    for group in dict.fromkeys(d.group_id for d in definitions):
        (repository.root / group).mkdir(parents=True, exist_ok=True)
    for group in dict.fromkeys(d.group_id for d in definitions):
        for order, definition in enumerate(d for d in definitions if d.group_id == group):
            folder = repository.root / group / definition.name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / TEST_FILE).write_text(
                json.dumps(document(definition, order), ensure_ascii=False, indent=2),
                encoding="utf-8")
            if body:
                (folder / SCENARIO_FILE).write_text(body, encoding="utf-8", newline="")
    repository.reload()
    return definitions
