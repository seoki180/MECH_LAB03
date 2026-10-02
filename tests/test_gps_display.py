"""GPS 좌표 표시 형식 검증.

표시는 소수점 아래 2자리까지만 보여주고 **버린다**(반올림하지 않는다). 올림이
일어나면 실제로 지나지 않은 자리를 가리키게 된다.

기록·복사는 이 형식과 무관하게 원본 정밀도를 유지한다. 화면 표시를 줄였다고
저장된 자료의 정밀도가 깎이면 안 된다.
"""

from experiment_app.application.copy_service import CopyService
from experiment_app.domain.session import Session, ExecutionSnapshot, State
from experiment_app.domain.telemetry import GpsFix, TelemetrySnapshot
# pytest가 Test로 시작하는 이름을 테스트 클래스로 오해하므로 별칭으로 들여온다.
from experiment_app.domain.test_definition import TestDefinition as Definition
from experiment_app.ui.panes.gps_map import GpsMapPane, format_coordinate


def test_coordinates_are_truncated_not_rounded():
    """반올림하면 가 보지 않은 자리를 가리킨다. 남은 자리는 버린다."""
    assert format_coordinate(37.4599) == "37.45", "37.46으로 올리지 않는다"
    assert format_coordinate(126.6492) == "126.64"
    assert format_coordinate(37.999) == "37.99"


def test_truncation_moves_toward_zero_for_negative_coordinates():
    """남위·서경에서도 절댓값이 커지지 않는다."""
    assert format_coordinate(-37.4599) == "-37.45"
    assert format_coordinate(-0.019) == "-0.01"


def test_exact_and_short_values_keep_two_digits():
    assert format_coordinate(37.0) == "37.00"
    assert format_coordinate(37.4) == "37.40"
    assert format_coordinate(0.0) == "0.00"


def test_missing_coordinate_is_a_dash_not_zero():
    """값이 없는 것과 0은 다르다."""
    assert format_coordinate(None) == "—"


def test_header_text_uses_two_digits():
    fix = GpsFix("s1", 37.45093161, 126.64926044, "2026-10-02T05:00:00+00:00", 1.0, "정상")
    snapshot = TelemetrySnapshot("s1", (), fix, ((37.45093161, 126.64926044),))
    assert GpsMapPane.coordinate_text(snapshot) == "위도 37.45, 경도 126.64"


def test_header_text_without_a_fix_falls_back_to_the_last_track_point():
    snapshot = TelemetrySnapshot("s1", (), None, ((37.45093161, 126.64926044),))
    assert GpsMapPane.coordinate_text(snapshot) == "위도 37.45, 경도 126.64 (마지막 유효)"


def test_header_text_with_nothing_received_shows_dashes():
    snapshot = TelemetrySnapshot("s1", (), None, ())
    assert GpsMapPane.coordinate_text(snapshot) == "위도 —, 경도 —"


def test_copied_data_keeps_full_precision():
    """화면 표시를 2자리로 줄여도 복사·기록 자료의 정밀도는 그대로다."""
    class FixedClock:
        def now_utc(self):
            return "2026-10-02T05:00:00+00:00"

        def monotonic(self):
            return 10.0

    latitude, longitude = 37.45093161524, 126.64926044272
    fix = GpsFix("s1", latitude, longitude, "2026-10-02T05:00:00+00:00", 1.0, "정상")
    snapshot = TelemetrySnapshot("s1", (), fix, ((latitude, longitude),))
    definition = Definition("t1", "g1", 1, "시험")
    session = Session("s1", ExecutionSnapshot(definition, (), ())).transition(State.READY)
    text = CopyService(FixedClock()).build_tsv(session, snapshot, set(), include_gps=True)
    assert repr(latitude) in text, "원본 정밀도가 그대로 복사되어야 한다"
    assert repr(longitude) in text
