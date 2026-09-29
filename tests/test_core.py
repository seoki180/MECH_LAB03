from dataclasses import replace
import csv
import io
import json
import sqlite3
import time
from threading import enumerate as threads
import pytest

from experiment_app.bootstrap import build_services
from experiment_app.demo.fixtures import fixtures, schemas, CHANNELS
from experiment_app.domain.edit_policy import EditPolicy
from experiment_app.domain.test_definition import FieldPatch, AppError
from experiment_app.domain.test_definition import DEFAULT_EXPERIMENT_DATA
from experiment_app.domain.session import State
from experiment_app.domain.telemetry import SensorSample
from experiment_app.infrastructure.repositories import LocalTestRepository
from experiment_app.application.test_service import TestService as DefinitionService
from experiment_app.presentation.view_models import metric_model
from experiment_app.infrastructure.tiles import MBTilesSource, MapPackSet
from experiment_app.ui.adapters.mercator import (lonlat_to_pixel, pixel_to_lonlat, ground_resolution,
                                                 world_size, zoom_about)


@pytest.fixture
def services(tmp_path):
    main, experiment = build_services(tmp_path, duration=0.16)
    yield main, experiment
    session = experiment.sessions.view()
    if session:
        experiment.sessions.stop(session.session_id)
    if experiment.sessions.worker:
        experiment.sessions.worker.join(4)
    main.dispose()


def wait(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "background operation timed out"
        time.sleep(0.01)


def prepare(main, experiment):
    definition = main.service.repository.list()[0]
    return experiment.sessions.prepare(definition.id, definition.revision)


def test_patch_preserves_unrelated_values_and_revision(services):
    main, _ = services
    original = main.service.repository.list()[0]
    step = original.spec_items[1]
    saved = main.service.save_patch(FieldPatch(original.id, 1, {f"spec/{step.id}/target": "44.25"}))
    assert saved.revision == 2
    assert saved.spec_items[0] == original.spec_items[0]
    assert saved.spec_items[2] == original.spec_items[2]
    assert dict(saved.spec_items[1].values) == {**dict(step.values), "target": 44.25}
    with pytest.raises(AppError, match="revision"):
        main.service.save_patch(FieldPatch(original.id, 1, {"name": "conflict"}))


def test_experiment_data_edit_persistence_and_session_snapshot(services, tmp_path):
    main, experiment = services
    original = main.service.repository.get("demo-0")
    assert original.experiment_data == DEFAULT_EXPERIMENT_DATA
    saved = main.service.save_patch(FieldPatch(original.id, original.revision, {
        "data/point_angle/Accel/1": "-7.25",
        "data/calibration_data/0": "0.125",
        "data/limit_point/Brake": "-6",
        "data/zero_brake_angle": "-15",
    }))
    assert saved.experiment_data == {
        "point_angle": {"Zero": [0, -19], "Accel": [22, -7.25], "Brake": [-9.5, -16.6]},
        "calibration_data": [0.125, -13.496798692559123],
        "limit_point": {"Accel": -0.2, "Brake": -6.0},
        "zero_brake_angle": -15.0,
    }
    session = experiment.sessions.prepare(saved.id, saved.revision)
    later = main.service.save_patch(FieldPatch(saved.id, saved.revision,
                                              {"data/zero_brake_angle": "-20"}))
    assert session.snapshot.definition.experiment_data["zero_brake_angle"] == -15.0
    assert later.experiment_data["zero_brake_angle"] == -20.0
    restored = LocalTestRepository(tmp_path / "tests.json", {}, [])
    assert restored.get(saved.id).experiment_data == later.experiment_data


def test_experiment_data_rejects_invalid_or_unknown_fields(services):
    main, _ = services
    original = main.service.repository.get("demo-0")
    for changes in ({"data/point_angle/Zero/0": "nan"},
                    {"data/calibration_data/2": 1},
                    {"data/limit_point/Accel": "bad"}):
        with pytest.raises(AppError) as error:
            main.service.save_patch(FieldPatch(original.id, original.revision, changes))
        assert error.value.code == "VALIDATION_FAILED"
    assert main.service.repository.get(original.id) == original


def test_legacy_saved_test_gets_experiment_data(tmp_path):
    from dataclasses import asdict
    groups, definitions = fixtures()
    legacy = asdict(definitions[0])
    legacy.pop("experiment_data")
    legacy.pop("ar_trapezoidal_step")
    legacy.pop("pf_straight_line")
    (tmp_path / "tests.json").write_text(json.dumps({"schema_version": 1, "groups": groups,
                                                      "tests": [legacy]}), encoding="utf-8")
    restored = LocalTestRepository(tmp_path / "tests.json", {}, [])
    assert restored.get(legacy["id"]).experiment_data == DEFAULT_EXPERIMENT_DATA
    assert restored.get(legacy["id"]).ar_trapezoidal_step is None
    assert restored.get(legacy["id"]).pf_straight_line is None


def test_service_enforces_partial_policy(services):
    main, _ = services
    original = main.service.repository.list()[0]
    path = f"spec/{original.spec_items[0].id}/target"
    service = DefinitionService(main.service.repository, schemas, lambda d: EditPolicy("partial", frozenset({path})))
    with pytest.raises(AppError) as error:
        service.save_patch(FieldPatch(original.id, 1, {"name": "not allowed", path: 3}))
    assert "name" in error.value.errors
    assert main.service.repository.get(original.id) == original
    assert service.save_patch(FieldPatch(original.id, 1, {path: 3})).revision == 2


@pytest.mark.parametrize("path,value", [("runs", "0"), ("runs", "1.5"), ("runs", True), ("name", "  "), ("type_id", "other")])
def test_validation(services, path, value):
    main, _ = services
    original = main.service.repository.list()[0]
    with pytest.raises(AppError):
        main.service.save_patch(FieldPatch(original.id, 1, {path: value}))


def test_save_failure_retains_draft_then_retries(services):
    main, _ = services
    main.select("demo-0")
    main.begin_edit()
    main.edit("name", "보존할 초안")
    main.service.repository.fail_save = True
    outcomes = []
    main.save(lambda result, error: outcomes.append((result, error)))
    wait(lambda: main.pending[0].done())
    main.poll()
    assert outcomes[-1][1].code == "STORAGE_FAILED"
    assert main.dirty and main.changes["name"] == "보존할 초안"
    assert main.definition.revision == 1
    main.service.repository.fail_save = False
    main.save(lambda result, error: outcomes.append((result, error)))
    wait(lambda: main.pending[0].done())
    main.poll()
    assert not main.dirty and main.definition.revision == 2


def test_snapshot_and_repeated_start_stop(services):
    main, experiment = services
    session = prepare(main, experiment)
    original = session.snapshot.definition
    main.service.save_patch(FieldPatch(original.id, original.revision, {"name": "changed"}))
    assert session.snapshot.definition == original
    assert experiment.sessions.start(session.session_id)
    assert not experiment.sessions.start(session.session_id)
    experiment.sessions.stop(session.session_id)
    experiment.sessions.stop(session.session_id)
    wait(experiment.sessions.is_idle)
    assert experiment.sessions.view().state == State.STOPPED
    assert len(experiment.sessions.results.list()) == 1
    assert not any(t.name.startswith("mechlab-acquisition") or t.name.startswith("mechlab-recorder") for t in threads())


def test_auto_complete_record_copy_and_restore(services, tmp_path):
    main, experiment = services
    session = prepare(main, experiment)
    experiment.sessions.start(session.session_id)
    wait(experiment.sessions.is_idle)
    assert experiment.sessions.view().state == State.COMPLETED
    data = list(csv.DictReader(io.StringIO(experiment.copy()), delimiter="\t"))
    assert len(data) == 6  # Four displayed channels, two GPS rows.
    assert {r["session_id"] for r in data} == {session.session_id}
    snapshot = experiment.telemetry.snapshot()
    sample = next(s for s in snapshot.samples if s.channel_id == "B.0")
    assert next(r for r in data if r["channel_id"] == sample.channel_id)["value"] == repr(sample.value)
    result = experiment.sessions.results.list()[0]
    records = [json.loads(line) for line in open(result["recording_path"], encoding="utf-8")]
    assert records[0]["snapshot"]["definition"]["id"] == "demo-0"
    assert records[0]["snapshot"]["definition"]["experiment_data"] == DEFAULT_EXPERIMENT_DATA
    assert len({r["channel_id"] for r in records[1:] if "channel_id" in r}) == 6
    restored_main, restored_experiment = build_services(tmp_path)
    assert len(restored_experiment.sessions.results.list()) == 1
    restored_main.dispose()


def test_recording_failure_is_incomplete(services):
    main, experiment = services
    experiment.sessions.duration = 4
    experiment.sessions.scenario = "기록 실패"
    session = prepare(main, experiment)
    experiment.sessions.start(session.session_id)
    wait(experiment.sessions.is_idle)
    assert experiment.sessions.view().state == State.ERROR
    result = experiment.sessions.results.list()[0]
    assert result["completeness"] == "불완전"
    assert "기록 실패" in result["end_reason"]


def test_catalog_persistence_and_order(services, tmp_path):
    main, _ = services
    repository = main.service.repository
    main.service.save_patch(FieldPatch("demo-0", 1, {"name": "영속 저장"}))
    repository.reorder("demo-0", 1)
    restored = LocalTestRepository(tmp_path / "tests.json", {}, [])
    assert restored.get("demo-0").name == "영속 저장"
    assert restored.list()[1].id == "demo-0"
    assert restored.get("demo-0").revision == 2


def test_out_of_order_and_old_session_samples(services):
    _, experiment = services
    telemetry = experiment.telemetry
    telemetry.reset("current")
    sample = SensorSample("current", "A.0", 5, "2026-01-01T00:00:00+00:00", 10, 1.2, "u")
    telemetry.ingest([sample, replace(sample, sequence=3, value=9), replace(sample, session_id="previous", sequence=6)])
    assert telemetry.snapshot().samples == (sample,)
    assert metric_model("A", "u", sample, 13).quality_label == "지연"
    assert metric_model("A", "u", sample, 16).quality_label == "연결 끊김"
    assert metric_model("A", "u", None, 16).value_text == "—"
    assert metric_model("A", "u", replace(sample, value=None, quality="값 오류"), 10).value_text == "—"
    telemetry.freeze_quality(16)
    assert telemetry.snapshot().samples[0].quality == "연결 끊김"
    assert metric_model("A", "u", telemetry.snapshot().samples[0], 100, live=False).quality_label == "연결 끊김"


def test_prepare_other_test_cannot_replace_active_session(services):
    main, experiment = services
    session = prepare(main, experiment)
    experiment.sessions.start(session.session_id)
    with pytest.raises(AppError):
        experiment.sessions.prepare("demo-1", 1, replace_ready=True)
    assert experiment.sessions.view().session_id == session.session_id


def test_four_displayed_metrics_and_copy_groups_preserve_recording(services):
    main, experiment = services
    session = prepare(main, experiment)
    waiting = experiment.metrics(experiment.telemetry.snapshot())
    assert list(waiting) == ["B.0", "B.1", "C.0", "C.1"]
    assert all(model.value_text == "—" for model in waiting.values())
    experiment.sessions.start(session.session_id)
    wait(experiment.sessions.is_idle)
    snapshot = experiment.telemetry.snapshot()
    models = experiment.metrics(snapshot)
    metadata = {channel: (label, unit) for _, channel, label, unit in experiment.sessions.channels}
    samples = {sample.channel_id: sample for sample in snapshot.samples}
    for channel, model in models.items():
        assert (model.label, model.unit) == metadata[channel]
        assert model.value_text == f"{samples[channel].value:.2f}"
        assert model.last_updated.endswith(" UTC")
    for part, expected in (("B", {"B.0", "B.1"}), ("C", {"C.0", "C.1"})):
        rows = list(csv.DictReader(io.StringIO(experiment.copy(part)), delimiter="\t"))
        assert {row["channel_id"] for row in rows} == expected
    assert len(snapshot.samples) == 6
    result = experiment.sessions.results.list()[0]
    with open(result["recording_path"], encoding="utf-8") as stream:
        records = [json.loads(line) for line in stream]
    assert "A.0" in json.dumps(records) and "A.1" in json.dumps(records)


def test_repeated_sessions_do_not_reuse_telemetry(services):
    main, experiment = services
    ids = []
    for _ in range(3):
        session = prepare(main, experiment)
        ids.append(session.session_id)
        assert experiment.telemetry.snapshot().samples == ()
        experiment.sessions.start(session.session_id)
        wait(experiment.sessions.is_idle)
        assert {s.session_id for s in experiment.telemetry.snapshot().samples} == {session.session_id}
    assert len(set(ids)) == 3
    assert len(experiment.sessions.results.list()) == 3


def test_gps_loss_keeps_last_valid_fix(services):
    from experiment_app.domain.telemetry import GpsFix
    _, experiment = services
    telemetry = experiment.telemetry
    telemetry.reset("gps-session")
    valid = GpsFix("gps-session", 37.4, 127.1, "2026-01-01T00:00:00+00:00", 1)
    lost = replace(valid, latitude=None, longitude=None, fix_quality="fix 없음", monotonic_received=2)
    telemetry.ingest([], valid)
    telemetry.ingest([], lost)
    snapshot = telemetry.snapshot()
    assert snapshot.gps.fix_quality == "fix 없음"
    assert snapshot.last_valid_gps == valid
    assert snapshot.track == ((37.4, 127.1),)


def test_invalid_values_never_enter_saved_definition(services):
    main, _ = services
    original = main.service.repository.list()[0]
    path = f"spec/{original.spec_items[0].id}/target"
    for value in ("nan", "inf", "-inf", "bad"):
        with pytest.raises(AppError):
            main.service.save_patch(FieldPatch(original.id, original.revision, {path: value}))
    assert main.service.repository.get(original.id) == original


def build_pack(path, tiles_rows, minzoom=10, maxzoom=16, bounds="127.09,37.39,127.11,37.41",
               image_format=None):
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE metadata (name text, value text);
        CREATE TABLE tiles (zoom_level integer, tile_column integer, tile_row integer, tile_data blob);
    """)
    connection.executemany("INSERT INTO metadata VALUES (?, ?)", [
        ("name", path.stem), ("minzoom", str(minzoom)), ("maxzoom", str(maxzoom)),
        ("bounds", bounds), ("attribution", "Contains modified Copernicus Sentinel data")]
        + ([("format", image_format)] if image_format else []))
    connection.executemany("INSERT INTO tiles VALUES (?, ?, ?, ?)", tiles_rows)
    connection.commit()
    connection.close()


@pytest.mark.parametrize("lon, lat", [(127.1, 37.4), (0.0, 0.0), (-122.4, 37.8), (139.7, -35.3)])
def test_mercator_round_trip(lon, lat):
    for zoom in (10, 16, 19):
        x, y = lonlat_to_pixel(lon, lat, zoom)
        back_lon, back_lat = pixel_to_lonlat(x, y, zoom)
        assert back_lon == pytest.approx(lon, abs=1e-6)
        assert back_lat == pytest.approx(lat, abs=1e-6)


def test_mercator_matches_known_tile_and_resolution():
    # z1에서 북동 사분면의 좌상단 모서리는 세계 픽셀 (256, 256)이다.
    assert lonlat_to_pixel(0.0, 0.0, 1) == pytest.approx((256.0, 256.0))
    # Sentinel-2 원본은 10 m/px이므로 위도 37.4에서 z14가 원본 수준이다.
    assert ground_resolution(37.4, 14) == pytest.approx(7.6, abs=0.1)


def test_pack_reads_tile_with_tms_row_flip(tmp_path):
    path = tmp_path / "site.mbtiles"
    # XYZ (12, 3, 5)는 MBTiles 행 2^12 - 1 - 5 = 4090에 들어간다.
    build_pack(path, [(12, 3, 4090, b"tile-bytes")])
    pack = MBTilesSource(path)
    assert pack.tile(12, 3, 5) == b"tile-bytes"
    assert pack.tile(12, 3, 4090) is None
    assert pack.center == pytest.approx((37.4, 127.1))
    pack.close()


def test_pack_set_loads_directory_and_skips_corrupt(tmp_path):
    build_pack(tmp_path / "a.mbtiles", [(12, 3, 4090, b"a")])
    build_pack(tmp_path / "b.mbtiles", [(12, 9, 4090, b"b")], minzoom=11, maxzoom=17)
    (tmp_path / "broken.mbtiles").write_bytes(b"not a database")
    tiles, failures = MapPackSet.load(tmp_path)
    assert [path.name for path, _ in failures] == ["broken.mbtiles"]
    assert tiles.available and (tiles.min_zoom, tiles.max_zoom) == (10, 17)
    assert tiles.tile(12, 3, 5) == b"a" and tiles.tile(12, 9, 5) == b"b"
    assert tiles.tile(12, 4, 5) is None
    assert tiles.attribution.startswith("Contains modified Copernicus")
    tiles.close()


def test_missing_pack_directory_falls_back_without_map(tmp_path):
    tiles, failures = MapPackSet.load(tmp_path / "absent")
    assert not failures and not tiles.available
    assert tiles.tile(14, 1, 1) is None


def test_world_size_accepts_fractional_zoom():
    # 핀치/휠 줌은 정수 사이를 지난다. 정수 입력 결과는 비트시프트 시절과 같아야 한다.
    assert world_size(14) == 256 * 2 ** 14
    assert world_size(14) < world_size(14.5) < world_size(15)
    x, y = lonlat_to_pixel(127.1, 37.4, 14.5)
    assert pixel_to_lonlat(x, y, 14.5) == pytest.approx((127.1, 37.4), abs=1e-6)


def test_zoom_about_centre_anchor_leaves_centre_untouched():
    center = (37.4, 127.1)
    assert zoom_about(center, (0, 0), 15, 17) == pytest.approx(center, abs=1e-9)


@pytest.mark.parametrize("old_zoom, new_zoom", [(15, 17), (17, 15), (15, 15.6), (16.4, 13.2)])
def test_zoom_about_keeps_the_anchor_under_the_cursor(old_zoom, new_zoom):
    center, offset = (37.4, 127.1), (180, -120)

    def anchor_at(middle, zoom):
        x, y = lonlat_to_pixel(middle[1], middle[0], zoom)
        lon, lat = pixel_to_lonlat(x + offset[0], y + offset[1], zoom)
        return (lat, lon)

    before = anchor_at(center, old_zoom)
    after = anchor_at(zoom_about(center, offset, old_zoom, new_zoom), new_zoom)
    assert after == pytest.approx(before, abs=1e-9)


def test_vector_pack_is_rejected_so_the_app_falls_back_to_the_grid(tmp_path):
    # 벡터 타일은 스타일과 렌더러가 있어야 그림이 된다. 앱이 디코딩을 시도하면 안 된다.
    build_pack(tmp_path / "vector.mbtiles", [(14, 1, 1, b"\x1f\x8b\x08not an image")], image_format="pbf")
    tiles, failures = MapPackSet.load(tmp_path)
    assert not tiles.available and tiles.tile(14, 1, 1) is None
    assert len(failures) == 1 and "pbf" in str(failures[0][1])


@pytest.mark.parametrize("image_format", ["png", "jpg", None])
def test_raster_and_legacy_packs_load(tmp_path, image_format):
    # format이 없는 구형 래스터 팩도 받아들인다. 거부는 알려진 벡터 포맷일 때만이다.
    directory = tmp_path / (image_format or "none")
    directory.mkdir()
    build_pack(directory / "raster.mbtiles", [(14, 3, (1 << 14) - 1 - 5, b"tile")], image_format=image_format)
    tiles, failures = MapPackSet.load(directory)
    assert not failures and tiles.available
    assert tiles.tile(14, 3, 5) == b"tile"


def test_robot_settings_legacy_patch_restore_snapshot_and_duplicate(services, tmp_path):
    main, experiment = services
    original = main.service.repository.get("demo-0")
    assert original.ar_trapezoidal_step is None and original.pf_straight_line is None
    saved = main.service.save_patch(FieldPatch(original.id, 1, {
        "robot/ar_trapezoidal_step/control": "Position",
        "robot/ar_trapezoidal_step/apply_rate": "526.32",
        "robot/ar_trapezoidal_step/use_gear_robot": False,
        "robot/pf_straight_line/start_x": "-1000",
        "robot/pf_straight_line/join_anywhere": True,
    }))
    session = experiment.sessions.prepare(saved.id, saved.revision)
    later = main.service.save_patch(FieldPatch(saved.id, saved.revision, {
        "robot/ar_trapezoidal_step/apply_rate": "",
        "robot/pf_straight_line/join_anywhere": None,
    }))
    assert later.experiment_data == original.experiment_data
    assert later.spec_items == original.spec_items
    assert later.ar_trapezoidal_step["apply_rate"] is None
    assert later.pf_straight_line["start_x"] == -1000
    assert later.pf_straight_line["join_anywhere"] is None
    assert session.snapshot.definition.ar_trapezoidal_step["apply_rate"] == 526.32
    groups, definitions = fixtures()
    restored = LocalTestRepository(tmp_path / "tests.json", groups, definitions)
    assert restored.get(saved.id) == later
    duplicate = main.service.duplicate(later)
    duplicate.pf_straight_line["start_x"] = 42
    assert later.pf_straight_line["start_x"] == -1000


@pytest.mark.parametrize("path,value", [
    ("robot/ar_trapezoidal_step/no_of_cycles", "1.5"),
    ("robot/ar_trapezoidal_step/apply_rate", "nan"),
    ("robot/pf_straight_line/join_anywhere", "false"),
    ("robot/pf_straight_line/unknown", 1),
])
def test_robot_settings_validation(services, path, value):
    main, _ = services
    with pytest.raises(AppError):
        main.service.save_patch(FieldPatch("demo-0", 1, {path: value}))


def test_explicit_edit_cancel_save_and_conflict(services):
    main, _ = services
    main.select("demo-0")
    original = main.definition
    main.edit("name", "blocked")
    assert not main.changes and not main.editing
    with pytest.raises(AppError) as error:
        main.service.save_patch(FieldPatch(original.id, 1, {"name": "blocked"}), require_edit=True)
    assert error.value.code == "EDIT_LOCKED"
    main.begin_edit()
    main.edit("name", "cancelled")
    main.cancel_edit()
    assert main.definition == original and not main.changes and not main.editing
    main.begin_edit()
    main.edit("name", "keep draft")
    main.service.save_patch(FieldPatch(original.id, 1, {"data/zero_brake_angle": -12}))
    outcomes = []
    main.save(lambda result, error: outcomes.append((result, error)))
    wait(lambda: main.pending[0].done())
    main.poll()
    assert outcomes[-1][1].code == "REVISION_CONFLICT"
    assert main.editing and main.changes["name"] == "keep draft"
    main.cancel_edit()
    main.select(original.id)
    main.begin_edit()
    main.edit("robot/pf_straight_line/control", "Robot steering")
    main.save(lambda result, error: outcomes.append((result, error)))
    wait(lambda: main.pending[0].done())
    main.poll()
    assert outcomes[-1][1] is None and not main.editing and not main.dirty
    main.new("group-a", duplicate=True)
    baseline = main.definition
    assert not main.editing
    main.begin_edit()
    main.edit("name", "new draft")
    main.cancel_edit()
    assert main.definition == baseline and main.definition.revision == 0
