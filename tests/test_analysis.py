"""결과 그래프가 쓰는 계산 검증.

확인 범위:
- 목표값 보간: 두 점 사이는 직선, 곡선 밖은 None(바깥으로 늘려 짐작하지 않는다).
- 편차 = 측정 − 목표. 목표값이 없는 시각은 건너뛴다(0으로 채우지 않는다).
- 값 없음·품질 이상 표본은 그래프에서 제외하고 몇 개인지 알린다.
- 시각 기준은 첫 표본을 0초로 둔 상대 시각.
- 기록 파일(.jsonl)에서 실행 시점 시나리오와 채널 표본을 읽어 온다.
- 실행 뒤 target.csv를 바꿔도 그래프는 그때의 목표값을 그린다.
"""
import json

import pytest

from experiment_app.application.analysis_service import AnalysisService
from experiment_app.bootstrap import build_services
from experiment_app.domain.analysis import Series, analyse, deviation_series, target_at
from experiment_app.domain.scenario import ScenarioPoint
from experiment_app.domain.session import State
from experiment_app.domain.test_definition import AppError

RAMP = (ScenarioPoint(0.0, 0.0), ScenarioPoint(10.0, 100.0))


# --- 목표값 보간 ---

def test_target_interpolates_between_points():
    assert target_at(RAMP, 0.0) == 0.0
    assert target_at(RAMP, 10.0) == 100.0
    assert target_at(RAMP, 2.5) == pytest.approx(25.0)


def test_target_outside_the_curve_is_unknown_not_zero():
    """곡선 밖은 자료에 없는 값이다. 끝값을 늘려 쓰지 않는다."""
    assert target_at(RAMP, -0.1) is None
    assert target_at(RAMP, 10.1) is None
    assert target_at((), 1.0) is None


def test_target_handles_repeated_timestamps():
    points = (ScenarioPoint(0.0, 1.0), ScenarioPoint(0.0, 2.0), ScenarioPoint(1.0, 3.0))
    assert target_at(points, 0.0) in (1.0, 2.0)
    assert target_at(points, 1.0) == 3.0


# --- 편차 ---

def test_deviation_is_measured_minus_target():
    measured = Series("속도", "km/h", ((0.0, 5.0), (5.0, 40.0), (10.0, 110.0)))
    deviation = deviation_series(measured, RAMP)
    assert [round(v, 6) for _, v in deviation.points] == [5.0, -10.0, 10.0]


def test_deviation_skips_times_without_a_target():
    """목표값이 없는 시각은 편차를 만들지 않는다. 0은 '정확히 맞춤'을 뜻한다."""
    measured = Series("속도", "km/h", ((0.0, 5.0), (20.0, 50.0)))
    deviation = deviation_series(measured, RAMP)
    assert [t for t, _ in deviation.points] == [0.0], "곡선 밖 20초는 빠진다"


def test_deviation_is_empty_without_a_scenario():
    measured = Series("속도", "km/h", ((0.0, 5.0), (1.0, 6.0)))
    assert not deviation_series(measured, ())


# --- 표본 정리 ---

def test_analyse_uses_the_first_sample_as_time_zero():
    samples = [(1000.5, 10.0, "정상"), (1001.5, 20.0, "정상"), (1002.5, 30.0, "정상")]
    result = analyse(samples, RAMP, "C.0", "현재 속도", "km/h")
    assert [t for t, _ in result.measured.points] == [0.0, 1.0, 2.0]
    assert result.measured.unit == "km/h"


def test_analyse_sorts_out_of_order_records():
    samples = [(1002.0, 30.0, "정상"), (1000.0, 10.0, "정상"), (1001.0, 20.0, "정상")]
    result = analyse(samples, RAMP, "C.0", "현재 속도", "km/h")
    assert [v for _, v in result.measured.points] == [10.0, 20.0, 30.0]


def test_analyse_excludes_missing_and_bad_quality_and_reports_the_count():
    """값 없음과 품질 이상은 0으로 바꾸지 않고 빼고, 몇 개인지 알린다."""
    samples = [(1000.0, 10.0, "정상"), (1001.0, None, "정상"),
               (1002.0, 99.0, "값 오류"), (1003.0, 30.0, "정상")]
    result = analyse(samples, RAMP, "C.0", "현재 속도", "km/h")
    assert [v for _, v in result.measured.points] == [10.0, 30.0]
    assert result.skipped == 2
    assert any("2개" in note for note in result.notes)


def test_analyse_accepts_valid_nmea_fix_but_rejects_missing_or_bad_quality():
    samples = [(1000.0, 18.0, "NMEA · INS_RTKFIXED"),
               (1001.0, None, "NMEA · fix 없음"),
               (1002.0, 20.0, "NMEA · fix 없음"),
               (1003.0, 25.0, "값 오류")]
    result = analyse(samples, RAMP, "C.0", "현재 속도", "km/h")
    assert result.measured.points == ((0.0, 18.0),)
    assert result.comparable
    assert result.skipped == 3


def test_analyse_without_measurements_explains_itself():
    result = analyse([], RAMP, "C.0", "현재 속도", "km/h")
    assert not result.measured and not result.comparable
    assert any("측정값이 없습니다" in note for note in result.notes)


def test_analyse_without_a_scenario_explains_itself():
    samples = [(1000.0, 10.0, "정상"), (1001.0, 20.0, "정상")]
    result = analyse(samples, (), "C.0", "현재 속도", "km/h")
    assert result.measured and not result.target and not result.comparable
    assert any("시험시나리오가 없어" in note for note in result.notes)


def test_analyse_warns_when_times_do_not_overlap_the_scenario():
    """측정은 있고 시나리오도 있는데 구간이 안 겹치면 그 사실을 말한다."""
    samples = [(1000.0, 10.0, "정상"), (1001.0, 20.0, "정상")]
    late = (ScenarioPoint(100.0, 0.0), ScenarioPoint(110.0, 50.0))
    result = analyse(samples, late, "C.0", "현재 속도", "km/h")
    assert result.measured and result.target and not result.comparable
    assert any("겹치지 않아" in note for note in result.notes)


def test_series_ranges():
    series = Series("s", "u", ((1.0, -5.0), (3.0, 7.0)))
    assert series.time_range == (1.0, 3.0)
    assert series.value_range == (-5.0, 7.0)
    assert not Series("s", "u", ())


# --- 기록 파일에서 읽기 ---

@pytest.fixture
def finished(tmp_path):
    """한 번 실행을 끝낸 상태의 서비스와 세션 id."""
    main, experiment = build_services(tmp_path)
    sessions = experiment.sessions
    definition = main.service.repository.list()[0]
    session = sessions.prepare(definition.id, definition.revision)
    sessions.start(session.session_id)
    import time
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and len(experiment.telemetry.snapshot().samples) < 4:
        time.sleep(0.02)
    sessions.stop(session.session_id)
    sessions.worker.join(5)
    yield main, experiment, session.session_id
    main.dispose()


def test_analysis_reads_the_recording_and_the_execution_scenario(finished):
    main, experiment, session_id = finished
    result = experiment.analyse(session_id)
    assert result.measured, "기록에서 측정 곡선을 읽는다"
    assert result.target, "실행 시점 시나리오를 읽는다"
    assert result.comparable, "겹치는 구간에서 편차가 나온다"
    assert result.measured.label == "Demo C · 1"


def test_analysis_is_unaffected_by_editing_the_scenario_afterwards(finished):
    """실행 뒤 target.csv를 지워도 그래프는 그때의 목표값을 그린다."""
    main, experiment, session_id = finished
    before = experiment.analyse(session_id).target.points
    main.service.clear_scenario(main.service.repository.list()[0].id)
    after = experiment.analyse(session_id).target.points
    assert after == before and before


def test_analysis_reports_a_missing_recording(tmp_path):
    main, experiment = build_services(tmp_path)
    try:
        sessions = experiment.sessions
        definition = main.service.repository.list()[0]
        session = sessions.prepare(definition.id, definition.revision)
        sessions.start(session.session_id)
        sessions.stop(session.session_id)
        sessions.worker.join(5)
        path = sessions.results.get(session.session_id)["recording_path"]
        from pathlib import Path
        Path(path).unlink()
        with pytest.raises(AppError) as error:
            experiment.analyse(session.session_id)
        assert error.value.code == "RECORDING_MISSING"
    finally:
        main.dispose()


def test_analysis_skips_a_truncated_last_line(finished, tmp_path):
    """기록 중 강제 종료로 마지막 줄이 잘려도 앞의 자료를 버리지 않는다."""
    main, experiment, session_id = finished
    path = experiment.sessions.results.get(session_id)["recording_path"]
    with open(path, "a", encoding="utf-8") as handle:
        handle.write('{"channel_id": "C.0", "value": 1.0')  # 닫히지 않은 JSON
    result = experiment.analyse(session_id)
    assert result.measured, "깨진 줄만 건너뛰고 나머지를 읽는다"


def test_analysis_follows_the_current_channel_labels(finished):
    """장치 탭에서 소스를 바꾸면 그래프 라벨·단위도 그 채널을 따른다."""
    main, experiment, session_id = finished
    service = AnalysisService(experiment.sessions.results, experiment.sessions)
    assert service.channel_for("C")[1] == "C.0"
    experiment.sessions.set_sources(
        (("C", "C.0", "현재 속도", "km/h"),), experiment.sessions.sensors, experiment.sessions.gps)
    assert service.channel_for("C")[2] == "현재 속도"
    assert service.analyse_session(session_id).measured.unit == "km/h"
