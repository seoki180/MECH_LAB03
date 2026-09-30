"""시험 폴더 저장소와 시험시나리오 CSV 검증.

확인 범위: test/<시험목록>/<시험>/ profile 폴더 구조(test.json + target.csv),
폴더째 가져오기/내보내기, 시나리오가 없어도 실행되고 깨진 시나리오는 막히는지.
"""
import json
import time
from pathlib import Path

import pytest

from experiment_app.bootstrap import build_services
from experiment_app.demo.fixtures import demo_scenario, fixtures
from experiment_app.domain.scenario import format_scenario_csv, parse_scenario_csv
from experiment_app.domain.session import State
from experiment_app.domain.test_definition import AppError, FieldPatch
from experiment_app.infrastructure.test_folders import (SCENARIO_FILE, TEST_FILE,
                                                        FolderTestRepository, safe_name)

ASSET_TARGET = (Path(__file__).resolve().parents[1]
                / "asset" / "UI작업 관련자료" / "데이터 흐름" / "10km_target.csv")


@pytest.fixture
def scenario_csv(tmp_path):
    source = tmp_path / "scenario.csv"
    source.write_text("time,target_v\n0,4.82E-06\n0.1,2\n", encoding="utf-8")
    return source


@pytest.fixture
def services(tmp_path):
    main, experiment = build_services(tmp_path, duration=0.16)
    yield main, experiment
    if experiment.sessions.worker:
        experiment.sessions.worker.join(4)
    main.dispose()


# ---------------------------------------------------------------- 시나리오 CSV

def test_asset_target_csv_parses_as_scenario():
    """로컬에 제공된 양식 파일이 있으면 그대로 읽을 수 있어야 한다."""
    if not ASSET_TARGET.is_file():
        pytest.skip("저장소에서 제외된 참고 자료")
    points = parse_scenario_csv(ASSET_TARGET.read_text(encoding="utf-8-sig"), ASSET_TARGET.name)
    assert len(points) == 685
    assert points[0].time == 0.0
    assert points[1].time == pytest.approx(0.1)
    # 지수 표기(4.82E-06)와 CRLF, BOM이 섞여 있어도 값이 유지된다.
    assert points[0].target_v == pytest.approx(4.82e-06)
    assert points[-1].time > points[0].time


def test_scenario_round_trip_keeps_values(scenario_csv):
    points = parse_scenario_csv(scenario_csv.read_text(encoding="utf-8-sig"))
    assert parse_scenario_csv(format_scenario_csv(points)) == points


def test_scenario_csv_supports_bom_crlf_and_exponents():
    points = parse_scenario_csv("\ufefftime,target_v\r\n0,4.82E-06\r\n0.1,2\r\n")
    assert points[0].target_v == pytest.approx(4.82e-06)
    assert points[1].time == pytest.approx(0.1)


@pytest.mark.parametrize("body", [
    "",
    "t,v\n0,1\n",
    "time,target_v\n",
    "time,target_v\n0,notanumber\n",
    "time,target_v\n0,1\n0,2\n",   # 시각이 증가하지 않음
    "time,target_v\n1,1\n0,2\n",   # 뒤섞인 순서
    "time,target_v\n-1,1\n",
    "time,target_v\n0,nan\n",
])
def test_invalid_scenario_csv_is_rejected(body):
    with pytest.raises(AppError) as error:
        parse_scenario_csv(body, "bad.csv")
    assert error.value.code == "SCENARIO_INVALID"


# ---------------------------------------------------------------- 폴더 저장소

def test_each_test_is_one_profile_folder(services, tmp_path):
    """시험 하나가 폴더 하나이고, 두 자료가 그 안에 고정된 이름으로 들어간다."""
    main, _ = services
    repository = main.service.repository
    root = tmp_path / "test"
    groups = repository.groups()
    assert sorted(p.name for p in root.iterdir()) == sorted(groups)
    for definition in repository.list():
        folder = repository.folder_of(definition.id)
        assert folder.parent.name == definition.group_id
        assert folder.name == definition.name
        # 안쪽 이름은 시험 이름과 무관하게 고정이다.
        assert sorted(p.name for p in folder.iterdir()) == [SCENARIO_FILE, TEST_FILE]
        body = json.loads((folder / TEST_FILE).read_text(encoding="utf-8"))
        assert body["name"] == definition.name and body["schema_version"] == 3


def test_rename_moves_the_whole_profile_folder(services, tmp_path):
    """이름을 바꾸면 폴더째 옮겨진다. 안쪽 자료는 이름이 고정이라 짝이 깨지지 않는다."""
    main, _ = services
    repository = main.service.repository
    original = repository.get("demo-0")
    old = repository.folder_of(original.id)
    saved = main.service.save_patch(FieldPatch(original.id, original.revision, {"name": "이름 바뀐 시험"}))
    new = repository.folder_of(saved.id)
    assert new.name == "이름 바뀐 시험"
    assert not old.exists()
    assert (new / TEST_FILE).is_file() and (new / SCENARIO_FILE).is_file()
    assert len(repository.scenario(saved.id)) == len(demo_scenario())


def test_renaming_the_folder_outside_the_app_renames_the_test(services, tmp_path):
    """탐색기에서 폴더 이름을 바꾸면 그것이 곧 시험 이름이 된다."""
    main, _ = services
    repository = main.service.repository
    folder = repository.folder_of("demo-0")
    folder.replace(folder.with_name("밖에서 바꾼 이름"))
    repository.reload()
    assert repository.get("demo-0").name == "밖에서 바꾼 이름"
    # 시나리오는 폴더를 따라갔으므로 그대로 읽힌다.
    assert len(repository.scenario("demo-0")) == len(demo_scenario())


def test_failed_save_does_not_rename_the_folder(services, tmp_path):
    """저장이 실패하면 폴더 이름도 그대로여야 한다. 반쯤 바뀐 상태를 남기지 않는다."""
    main, _ = services
    repository = main.service.repository
    original = repository.get("demo-0")
    before = repository.folder_of(original.id)
    repository.fail_save = True
    with pytest.raises(AppError) as error:
        main.service.save_patch(FieldPatch(original.id, original.revision, {"name": "실패할 이름"}))
    assert error.value.code == "STORAGE_FAILED"
    assert before.is_dir() and not before.with_name("실패할 이름").exists()
    repository.fail_save = False
    repository.reload()
    assert repository.get(original.id).name == "데모 시험 01"


def test_files_added_outside_the_app_are_picked_up(services, tmp_path):
    main, _ = services
    repository = main.service.repository
    group = tmp_path / "test" / next(iter(repository.groups()))
    body = json.loads(repository.path_of("demo-0").read_text(encoding="utf-8"))
    body["id"] = "hand-made"
    body.pop("name", None)          # 이름은 폴더가 정한다
    folder = group / "손으로 넣은 시험"
    folder.mkdir()
    (folder / TEST_FILE).write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    repository.reload()
    assert repository.get("hand-made").name == "손으로 넣은 시험"
    assert not repository.load_errors


def test_folder_without_test_json_is_not_a_test(services, tmp_path):
    """시험목록 폴더에 사용자가 둔 다른 폴더를 시험으로 착각하지 않는다."""
    main, _ = services
    repository = main.service.repository
    group = tmp_path / "test" / next(iter(repository.groups()))
    (group / "참고자료").mkdir()
    (group / "참고자료" / "메모.txt").write_text("시험이 아님", encoding="utf-8")
    errors = repository.reload()
    assert not errors
    assert len(repository.list()) == 6


def test_broken_file_is_reported_without_hiding_the_rest(services, tmp_path):
    main, _ = services
    repository = main.service.repository
    group = tmp_path / "test" / next(iter(repository.groups()))
    broken = group / "깨진 시험"
    broken.mkdir()
    (broken / TEST_FILE).write_text("{ not json", encoding="utf-8")
    errors = repository.reload()
    assert [path.parent.name for path, _ in errors] == ["깨진 시험"]
    assert len(repository.list()) == 6


def test_duplicate_id_in_two_folders_does_not_overwrite(services, tmp_path):
    """폴더를 복사해 붙여 넣어도 서로 다른 시험으로 남는다."""
    import shutil
    main, _ = services
    repository = main.service.repository
    source = repository.folder_of("demo-0")
    shutil.copytree(source, source.with_name("데모 시험 01 복사본"))
    repository.reload()
    names = sorted(d.name for d in repository.list())
    assert "데모 시험 01" in names and "데모 시험 01 복사본" in names
    assert len({d.id for d in repository.list()}) == len(repository.list())


def test_group_add_and_delete_use_real_folders(services, tmp_path):
    main, _ = services
    repository = main.service.repository
    group_id = repository.add_group("새 시험목록")
    assert (tmp_path / "test" / "새 시험목록").is_dir()
    with pytest.raises(AppError):
        repository.add_group("새 시험목록")
    repository.delete_group(group_id)
    assert not (tmp_path / "test" / "새 시험목록").exists()


def test_reorder_persists_across_reload(services, tmp_path):
    main, _ = services
    repository = main.service.repository
    first = repository.list()[0]
    repository.reorder(first.id, 1)
    assert repository.list()[1].id == first.id
    assert FolderTestRepository(tmp_path / "test").list()[1].id == first.id


@pytest.mark.parametrize("name, expected", [
    ("보통 이름", "보통 이름"),
    ("a/b:c*", "a_b_c_"),
    ("   ", "이름없음"),
    ("CON", "이름없음"),
])
def test_safe_name_strips_forbidden_characters(name, expected):
    assert safe_name(name) == expected


# ------------------------------------------------------------ 가져오기/내보내기

def test_export_then_import_carries_the_whole_folder(services, tmp_path):
    main, _ = services
    service = main.service
    outbox = tmp_path / "outbox"
    written = service.export("demo-0", outbox)
    assert written.name == "데모 시험 01"
    assert sorted(p.name for p in written.iterdir()) == [SCENARIO_FILE, TEST_FILE]
    target_group = list(service.repository.groups())[1]
    imported = service.import_test(written, target_group)
    assert imported.group_id == target_group
    assert imported.id != "demo-0"           # 같은 id를 덮어쓰지 않는다
    assert imported.name == "데모 시험 01"
    assert service.scenario(imported.id) == service.scenario("demo-0")


def test_import_rejects_broken_scenario_and_leaves_nothing_behind(services, tmp_path):
    main, _ = services
    service = main.service
    outbox = tmp_path / "outbox"
    written = service.export("demo-0", outbox)
    (written / SCENARIO_FILE).write_text("time,target_v\n0,bad\n", encoding="utf-8")
    before = len(service.repository.list())
    with pytest.raises(AppError) as error:
        service.import_test(written, list(service.repository.groups())[1])
    assert error.value.code == "SCENARIO_INVALID"
    service.repository.reload()
    assert len(service.repository.list()) == before


def test_import_rejects_a_folder_without_test_json(services, tmp_path):
    """아무 폴더나 시험으로 들여오지 않는다."""
    main, _ = services
    service = main.service
    stray = tmp_path / "그냥 폴더"
    stray.mkdir()
    (stray / "메모.txt").write_text("시험 아님", encoding="utf-8")
    with pytest.raises(AppError) as error:
        service.import_test(stray, next(iter(service.repository.groups())))
    assert error.value.code == "VALIDATION_FAILED"


def test_flat_layout_migrates_into_profile_folders(tmp_path):
    """예전 배치(이름.json + 이름.csv)를 profile 폴더로 한 번에 옮긴다."""
    from dataclasses import asdict
    root = tmp_path / "test"
    group = root / "예전 목록"
    group.mkdir(parents=True)
    _, definitions = fixtures()
    body = {"schema_version": 2, "order": 0, **asdict(definitions[0])}
    (group / "옛날 시험.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    (group / "옛날 시험.csv").write_text(
        format_scenario_csv(demo_scenario()), encoding="utf-8", newline="")

    repository = FolderTestRepository(root)
    folder = group / "옛날 시험"
    assert folder.is_dir()
    assert sorted(p.name for p in folder.iterdir()) == [SCENARIO_FILE, TEST_FILE]
    assert not (group / "옛날 시험.json").exists() and not (group / "옛날 시험.csv").exists()
    loaded = repository.list()[0]
    assert loaded.name == "옛날 시험"
    assert len(repository.scenario(loaded.id)) == len(demo_scenario())


def test_set_and_clear_scenario(services, scenario_csv):
    main, _ = services
    service = main.service
    points = service.set_scenario("demo-0", scenario_csv)
    assert len(points) == 2
    assert service.scenario("demo-0") == points
    service.clear_scenario("demo-0")
    assert service.scenario("demo-0") == ()


# ------------------------------------------------------------------- 실행 연결

def test_prepare_fixes_the_scenario_and_ignores_later_edits(services, scenario_csv):
    main, experiment = services
    session = experiment.sessions.prepare("demo-0", 1)
    original = session.snapshot.scenario
    assert original == demo_scenario()
    main.service.set_scenario("demo-0", scenario_csv)
    assert session.snapshot.scenario == original


def test_missing_scenario_still_runs_without_targets(services):
    """시나리오는 선택이다. 없으면 목표값 없이 실행한다."""
    main, experiment = services
    main.service.clear_scenario("demo-1")
    session = experiment.sessions.prepare("demo-1", 1)
    assert session.snapshot.scenario == (), "목표값 없이 준비된다"
    assert experiment.sessions.start(session.session_id)
    experiment.sessions.stop(session.session_id)
    experiment.sessions.worker.join(4)
    assert experiment.sessions.view().state == State.STOPPED


def test_broken_scenario_still_blocks_start(services):
    """읽지 못하는 파일은 '없음'과 다르다. 의도한 목표값 없이 돌지 않게 막는다."""
    main, experiment = services
    main.service.scenario_path("demo-1").write_text("time,target_v\n0,bad\n", encoding="utf-8")
    with pytest.raises(AppError) as error:
        experiment.sessions.prepare("demo-1", 1)
    assert error.value.code == "SCENARIO_INVALID"
    assert experiment.sessions.view() is None


def test_robot_is_told_there_is_no_scenario(services):
    """시나리오가 없으면 빈 목록이 아니라 None을 보낸다. 둘은 뜻이 다르다."""
    main, experiment = services
    main.service.clear_scenario("demo-1")
    session = experiment.sessions.prepare("demo-1", 1)
    experiment.sessions.start(session.session_id)
    for _ in range(400):
        if experiment.sessions.robot.transport.configuration is not None:
            break
        time.sleep(0.01)
    configuration = experiment.sessions.robot.transport.configuration
    experiment.sessions.stop(session.session_id)
    assert configuration is not None
    assert configuration["scenario"] is None


def test_robot_receives_the_scenario_with_the_configuration(services):
    main, experiment = services
    session = experiment.sessions.prepare("demo-0", 1)
    experiment.sessions.start(session.session_id)
    deadline_reached = False
    import time
    for _ in range(400):
        if experiment.sessions.robot.transport.configuration is not None:
            break
        time.sleep(0.01)
    else:
        deadline_reached = True
    configuration = experiment.sessions.robot.transport.configuration
    experiment.sessions.stop(session.session_id)
    assert not deadline_reached
    assert configuration["scenario"][0] == [0.0, 0.0]
    assert len(configuration["scenario"]) == len(demo_scenario())


def test_presenter_scenario_view_model(services):
    main, _ = services
    main.select("demo-0")
    assert main.scenario.runnable and "301행" in main.scenario.summary
    main.service.clear_scenario("demo-0")
    main.refresh_scenario()
    # 없음은 실행을 막지 않는다. 다만 오류와 구별되는 상태로 표시한다.
    assert main.scenario.state == "none" and main.scenario.runnable
    path = main.service.scenario_path("demo-0")
    path.write_text("time,target_v\n0,bad\n", encoding="utf-8")
    main.refresh_scenario()
    assert main.scenario.state == "error" and not main.scenario.runnable
