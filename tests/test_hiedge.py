"""HI-EDGE LAN WebSocket 소스 검증.

실장비가 실내에서 fix를 얻지 못해 ``valid:false`` 만 보내므로, 유효 속도·위치·변위가
들어오는 경로는 같은 규격을 흉내내는 지역 서버와 실측 메시지 모양으로 확인한다.
실장비 연결 자체는 수동 검증(``python -m tools.lan_probe``)으로 확인했다.

메시지 모양은 장치 문서(``/static/api-reference``)와 실측 프레임을 따른다. 위치는
최상위가 아니라 ``position`` 객체 안에 있고, 변위는 등록된 기준점마다
``position_offsets[]`` 항목으로 온다.

확인 범위:
- 유효 메시지가 6채널 표본으로 바뀐다.
- ``valid:false`` 는 값이 None이고 0으로 채우지 않는다.
- ``position.valid`` 가 false면 좌표 값이 남아 있어도 쓰지 않는다.
- 변위·직선거리는 **장치가 준 값을 그대로** 쓴다(앱이 좌표로 다시 계산하지 않는다).
- 누적거리는 장치 ENU의 차분 적분이고, 정지 중 흔들림과 단절 구간은 더하지 않는다.
- 기준점이 없거나 사라지면 지어내지 않고 사유를 남긴다.
- 규격이 다르거나 연결이 안 되면 구조화된 오류 코드로 거부한다.
"""

import json
import ssl
import threading

import pytest

from experiment_app.domain.test_definition import AppError
from experiment_app.infrastructure import hiedge, hiedge_status
from experiment_app.infrastructure.sources import SystemClock

pytest.importorskip("websockets")


SCHEMA = hiedge.SCHEMA
REFERENCE_ID = "ref-0001"


def offset(reference_id=REFERENCE_ID, *, valid=True, east=None, north=None,
           distance=None, reason="ok"):
    """장치가 보내는 position_offsets 항목 하나."""
    return {"reference_id": reference_id, "valid": valid, "reason": reason,
            "delta_lat_deg": None, "delta_lon_deg": None,
            "north_m": north, "east_m": east, "horizontal_distance_m": distance,
            "altitude_valid": False, "altitude_reason": "unavailable",
            "delta_altitude_m": None, "spatial_distance_m": None}


def speed_message(seq, *, valid=True, kmh=36.0, latitude=None, longitude=None, gap=False,
                  position_valid=None, offsets=None, altitude=None):
    """실측 프레임과 같은 모양의 speed 메시지.

    ``position_valid`` 를 따로 줄 수 있게 한 이유는, 좌표 값이 남아 있는데 무효인
    경우(``reason: stale`` 등)를 시험해야 하기 때문이다.
    """
    if position_valid is None:
        position_valid = latitude is not None
    message = {"schema": SCHEMA, "type": "speed", "stream_id": "test-stream", "seq": seq,
               "publication_hz": 50, "output_time_utc": f"2026-10-02T05:00:{seq % 60:02d}.000000Z",
               "output_monotonic_s": float(seq) * 0.02, "source_interval_s": 0.02,
               "source_gap": gap, "valid": valid, "input_connected": True, "input_age_s": 0.01,
               "estimate": None,
               "position": {"valid": position_valid,
                            "reason": "ok" if position_valid else "invalid_solution",
                            "source": "GGA", "fix_type": "rtk_fixed" if position_valid else "no_fix",
                            "satellites_used": 20 if position_valid else 0,
                            "latitude_deg": latitude, "longitude_deg": longitude,
                            "altitude_m": altitude,
                            "altitude_reference": "mean_sea_level" if altitude is not None else None,
                            "measurement_time_utc": "2026-10-02T05:00:00Z"},
               "attitude": {"valid": False, "reason": "unavailable"},
               "reference_set_id": "set-1",
               "position_offsets": list(offsets or ())}
    if valid:
        message["estimate"] = {"speed_kmh": kmh, "speed_mps": kmh / 3.6,
                               "measurement_kind": "measurement"}
    return message


# --- 메시지 → 표본 변환 (소켓 없이) ---

def make_source(reference_id=REFERENCE_ID):
    """소켓을 열지 않고 변환만 확인하기 위한 소스.

    기준점은 ``prepare()`` 가 장치에 등록해서 받는 값이다. 여기서는 등록을 건너뛰고
    id만 꽂는다.
    """
    source = hiedge.HiEdgeSpeedSource(SystemClock(), verify=False)
    source.reference_id = reference_id
    return source


def values(source, session_id, message):
    source.submit(message)
    samples = source.read(session_id, 0.0, 1, "정상")
    return {sample.channel_id: sample for sample in samples}


def test_valid_speed_without_offset_fills_only_speed_channels():
    source = make_source()
    got = values(source, "s1", speed_message(1, kmh=36.0))
    assert len(got) == 6, "채널 수는 변위 유무와 무관하게 6개다"
    assert got["A.0"].value == pytest.approx(36.0)
    assert got["C.0"].value == pytest.approx(36.0)
    # 기준점 항목이 없으면 거리·방향을 지어내지 않는다.
    for channel in ("A.1", "B.0", "B.1", "C.1"):
        assert got[channel].value is None, f"{channel}은 변위 없이 계산할 수 없다"
    assert "기준점 사라짐" in got["A.0"].quality


def test_invalid_message_is_not_reported_as_zero():
    source = make_source()
    got = values(source, "s1", speed_message(1, valid=False))
    assert all(sample.value is None for sample in got.values())
    assert "사용 불가" in got["A.0"].quality


def test_source_time_and_received_time_are_both_kept():
    """장비 출력 시각과 수신 시각을 분리해 남긴다."""
    source = make_source()
    got = values(source, "s1", speed_message(7))
    sample = got["A.0"]
    assert sample.source_time_utc == "2026-10-02T05:00:07.000000Z"
    assert sample.received_time_utc != sample.source_time_utc
    assert sample.sequence == 7, "장비 seq를 표본 순번으로 쓴다"


# --- 변위는 장치가 보낸 값을 그대로 쓴다 ---

def test_displacement_comes_from_the_device_offset():
    """앱이 좌표로 다시 계산하지 않고 장치 값을 그대로 싣는다."""
    source = make_source()
    got = values(source, "s1", speed_message(
        1, kmh=36.0, latitude=37.0, longitude=127.0,
        offsets=[offset(east=3.0, north=4.0, distance=5.0)]))
    assert got["B.0"].value == pytest.approx(3.0), "횡방향은 east_m 그대로"
    assert got["B.1"].value == pytest.approx(4.0), "종방향은 north_m 그대로"
    assert got["A.1"].value == pytest.approx(5.0), "직선거리는 horizontal_distance_m 그대로"


def test_other_reference_offsets_are_ignored():
    """장치에는 남의 기준점도 등록돼 있다. 우리 id만 읽는다."""
    source = make_source()
    got = values(source, "s1", speed_message(
        1, kmh=36.0, latitude=37.0, longitude=127.0,
        offsets=[offset("남의-기준점", east=999.0, north=999.0, distance=999.0),
                 offset(east=1.0, north=2.0, distance=3.0)]))
    assert got["A.1"].value == pytest.approx(3.0)
    assert got["B.0"].value == pytest.approx(1.0)


def test_invalid_offset_is_not_reported_as_zero():
    source = make_source()
    got = values(source, "s1", speed_message(
        1, kmh=36.0, latitude=37.0, longitude=127.0,
        offsets=[offset(valid=False, reason="position_invalid")]))
    for channel in ("A.1", "B.0", "B.1", "C.1"):
        assert got[channel].value is None
    assert "position_invalid" in got["A.0"].quality
    assert got["A.0"].value == pytest.approx(36.0), "속도는 변위와 따로 유효하다"


def test_missing_reference_is_reported_not_guessed():
    """등록해 둔 기준점이 목록에서 사라지면 사실대로 알린다."""
    source = make_source()
    got = values(source, "s1", speed_message(
        1, kmh=36.0, latitude=37.0, longitude=127.0,
        offsets=[offset("남의-기준점", east=1.0, north=1.0, distance=1.4)]))
    assert got["A.1"].value is None
    assert "기준점 사라짐" in got["A.0"].quality


def test_no_reference_registered_is_reported():
    source = make_source(reference_id=None)
    got = values(source, "s1", speed_message(1, kmh=36.0, latitude=37.0, longitude=127.0))
    assert got["A.1"].value is None
    assert "기준점 없음" in got["A.0"].quality


# --- 누적 이동거리: 장치 ENU의 차분 적분 ---

def test_travelled_distance_integrates_device_enu():
    source = make_source()
    first = values(source, "s1", speed_message(
        1, kmh=36.0, offsets=[offset(east=0.0, north=0.0, distance=0.0)]))
    assert first["C.1"].value == pytest.approx(0.0), "첫 표본에는 더할 이전 점이 없다"
    second = values(source, "s1", speed_message(
        2, kmh=36.0, offsets=[offset(east=3.0, north=4.0, distance=5.0)]))
    assert second["C.1"].value == pytest.approx(5.0), "hypot(3,4)만큼 이동"
    third = values(source, "s1", speed_message(
        3, kmh=36.0, offsets=[offset(east=3.0, north=14.0, distance=14.3)]))
    assert third["C.1"].value == pytest.approx(15.0), "누적 5 + 10"


def test_travelled_distance_is_reset_for_a_new_session():
    source = make_source()
    values(source, "s1", speed_message(1, kmh=36.0, offsets=[offset(east=0.0, north=0.0, distance=0.0)]))
    values(source, "s1", speed_message(2, kmh=36.0, offsets=[offset(east=3.0, north=4.0, distance=5.0)]))
    fresh = values(source, "s2", speed_message(3, kmh=36.0, offsets=[offset(east=3.0, north=4.0, distance=5.0)]))
    assert fresh["C.1"].value == pytest.approx(0.0), "누적거리는 세션마다 다시 센다"
    assert fresh["A.1"].value == pytest.approx(5.0), "직선거리는 장치 기준점 기준이라 그대로다"


def test_stationary_jitter_does_not_accumulate_distance():
    """정지 중 변위가 흔들려도 누적거리가 늘지 않는다."""
    source = make_source()
    values(source, "s1", speed_message(1, kmh=0.0, offsets=[offset(east=0.0, north=0.0, distance=0.0)]))
    got = values(source, "s1", speed_message(2, kmh=0.2, offsets=[offset(east=0.05, north=0.05, distance=0.07)]))
    assert got["C.1"].value == pytest.approx(0.0, abs=0.001)
    # 직선거리는 기준점 대비 실제 차이이므로 0이 아니다. 둘은 다른 값이다.
    assert got["A.1"].value > 0


def test_offset_gap_does_not_bridge_travelled_distance():
    """변위가 빠진 구간의 경로는 알 수 없으므로 이어 붙이지 않는다."""
    source = make_source()
    values(source, "s1", speed_message(1, kmh=36.0, offsets=[offset(east=0.0, north=0.0, distance=0.0)]))
    values(source, "s1", speed_message(2, kmh=36.0, offsets=[offset(east=0.0, north=10.0, distance=10.0)]))
    values(source, "s1", speed_message(3, kmh=36.0, offsets=[offset(valid=False, reason="position_invalid")]))
    after = values(source, "s1", speed_message(4, kmh=36.0, offsets=[offset(east=0.0, north=30.0, distance=30.0)]))
    assert after["C.1"].value == pytest.approx(10.0), "끊기기 전 10 m만 남는다"
    assert after["A.1"].value == pytest.approx(30.0), "직선거리는 기준점 기준이라 이어진다"


def test_source_gap_is_visible_in_quality():
    source = make_source()
    got = values(source, "s1", speed_message(1, gap=True))
    assert "단절" in got["A.0"].quality


# --- 위치(GPS) ---

def test_gps_source_reports_no_fix_when_position_is_invalid():
    source = make_source()
    gps = hiedge.HiEdgeGpsSource(source)
    assert gps.read("s1", 0.0, "정상") is None, "아직 받은 것이 없으면 None"
    values(source, "s1", speed_message(1, kmh=36.0))
    fix = gps.read("s1", 0.0, "정상")
    assert fix is not None and fix.latitude is None, "위치 무효를 fix 없음으로 알린다"
    values(source, "s1", speed_message(2, kmh=36.0, latitude=37.0, longitude=127.0, altitude=60.5))
    fix = gps.read("s1", 0.0, "정상")
    assert fix.latitude == pytest.approx(37.0)
    assert fix.altitude == pytest.approx(60.5), "고도는 position.altitude_m에서 읽는다"


def test_stale_position_is_not_used_even_if_coordinates_remain():
    """valid가 false면 좌표가 남아 있어도 현재 위치로 쓰지 않는다."""
    source = make_source()
    gps = hiedge.HiEdgeGpsSource(source)
    values(source, "s1", speed_message(1, kmh=36.0, latitude=37.0, longitude=127.0,
                                       position_valid=False))
    fix = gps.read("s1", 0.0, "정상")
    assert fix.latitude is None, "무효한 위치를 지도에 찍지 않는다"


def test_altitude_without_a_reference_is_not_used():
    """고도 기준이 없으면 다른 고도와 비교할 수 없으므로 쓰지 않는다."""
    source = make_source()
    message = speed_message(1, kmh=36.0, latitude=37.0, longitude=127.0, altitude=60.5)
    message["position"]["altitude_reference"] = None
    values(source, "s1", message)
    fix = hiedge.HiEdgeGpsSource(source).read("s1", 0.0, "정상")
    assert fix.altitude is None


# --- TLS 설정 ---

def test_certificate_is_required_unless_verification_is_explicitly_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("MECHLAB_HOME", str(tmp_path))
    with pytest.raises(AppError) as caught:
        hiedge.tls_context(verify=True)
    assert caught.value.code == "LAN_CERT_MISSING"
    # 검증 생략은 호출부가 명시적으로 선택해야 한다. 조용히 끄지 않는다.
    assert hiedge.tls_context(verify=False).verify_mode == ssl.CERT_NONE


def test_unreachable_host_is_a_structured_error():
    with pytest.raises(AppError) as caught:
        # 라우팅되지 않는 주소. 짧은 timeout으로 끝낸다.
        hiedge.probe(host="192.0.2.1", port=8443, verify=False, timeout=1.5)
    assert caught.value.code in {"LAN_TIMEOUT", "LAN_UNAVAILABLE"}


# --- 기준점 수명 (장치 없이 가짜 클라이언트로) ---

class FakeReferences:
    """ReferenceClient와 같은 모양의 가짜. 호출 순서를 기록한다."""

    def __init__(self, free=7, fail=None):
        self.free, self.fail = free, fail
        self.calls = []
        self.registered = None
        self.deleted = []
        # 등록한 기준점이 장치에 남아 있는가. 다른 구독자가 지운 상황을 흉내낸다.
        self.present = True

    def free_slots(self):
        self.calls.append("free_slots")
        return self.free

    def exists(self, reference_id):
        self.calls.append("exists")
        return self.present and reference_id not in self.deleted

    def purge(self):
        self.calls.append("purge")
        return 2

    def register_current(self, name):
        self.calls.append("register")
        if self.fail:
            raise self.fail
        self.registered = name
        return {"reference_id": "new-ref", "name": name}

    def delete(self, reference_id):
        self.calls.append("delete")
        self.deleted.append(reference_id)


def test_reference_is_registered_on_start_and_kept_until_the_window_closes(monkeypatch):
    references = FakeReferences()
    source = hiedge.HiEdgeSpeedSource(SystemClock(), verify=False, references=references)
    # 소켓은 이 시험의 대상이 아니다. 연결 부분만 대체한다.
    monkeypatch.setattr(source, "connect", lambda: None)
    monkeypatch.setattr(source, "disconnect", lambda: None)
    source.prepare()
    assert references.calls == ["purge", "register"], "남은 것을 먼저 지우고 등록한다"
    assert references.registered.startswith("MECHLab-"), "우리 것임을 이름으로 밝힌다"
    assert source.reference_id == "new-ref"
    source.close()
    assert references.deleted == [], "세션이 끝나도 기준점은 살아 있다(단절 복구용)"
    assert source.reference_id == "new-ref"
    source.release()
    assert references.deleted == ["new-ref"], "창을 닫을 때 지운다"
    assert source.reference_id is None


def test_restart_after_a_dropout_keeps_the_same_reference(monkeypatch):
    """5초가 넘는 단절로 세션이 끝난 뒤 다시 시작해도 기준점이 그대로여야 한다.

    새로 등록하면 기준점이 '다시 시작한 자리'로 옮겨가 앞 구간의 변위와 이어지지 않는다.
    """
    references = FakeReferences()
    source = hiedge.HiEdgeSpeedSource(SystemClock(), verify=False, references=references)
    monkeypatch.setattr(source, "connect", lambda: None)
    monkeypatch.setattr(source, "disconnect", lambda: None)
    source.prepare()
    first = source.reference_id
    source.close()  # 단절로 세션 종료
    references.calls.clear()
    source.prepare()  # 사용자가 다시 시작
    assert source.reference_id == first, "같은 기준점으로 이어 받는다"
    assert references.calls == ["exists"], "다시 등록하지도, 남을 지우지도 않는다"
    assert references.deleted == []


def test_a_reference_deleted_by_someone_else_is_registered_again(monkeypatch):
    """우리가 멈춘 사이 누가 지웠으면 이어 쓸 수 없으므로 새로 잡는다."""
    references = FakeReferences()
    source = hiedge.HiEdgeSpeedSource(SystemClock(), verify=False, references=references)
    monkeypatch.setattr(source, "connect", lambda: None)
    monkeypatch.setattr(source, "disconnect", lambda: None)
    source.prepare()
    source.close()
    references.present = False  # 다른 구독자가 지웠다
    references.calls.clear()
    source.prepare()
    assert references.calls == ["exists", "purge", "register"]
    assert source.reference_id == "new-ref"


def test_failed_registration_closes_the_socket_and_stops_the_start(monkeypatch):
    references = FakeReferences(fail=AppError("LAN_REF_REJECTED", "정차 상태가 아닙니다."))
    source = hiedge.HiEdgeSpeedSource(SystemClock(), verify=False, references=references)
    closed = []
    monkeypatch.setattr(source, "connect", lambda: None)
    monkeypatch.setattr(source, "disconnect", lambda: closed.append(True))
    with pytest.raises(AppError) as caught:
        source.prepare()
    assert caught.value.code == "LAN_REF_REJECTED"
    assert closed, "등록이 실패하면 열어 둔 소켓을 닫는다"
    assert source.reference_id is None


class FakeStatus:
    """StatusClient와 같은 모양의 가짜. fix 상태를 바꿔 가며 시험한다."""

    def __init__(self, valid=True, kmh=0.0, label="RTK Fixed", satellites=20, error=None):
        self.valid, self.kmh = valid, kmh
        self.label, self.satellites = label, satellites
        self.error = error
        self.calls = 0

    def latest(self):
        self.calls += 1
        if self.error:
            raise self.error
        return {"schema": "edge-map.v1",
                "status": {"fix": {"solution_valid": self.valid, "stale": False,
                                   "stale_reason": None, "fix_state": "no_fix",
                                   "solution_label": self.label,
                                   "satellites_used": self.satellites,
                                   "speed_kmh": self.kmh},
                           "motion": {"valid": self.valid, "stale": False,
                                      "speed_kmh": self.kmh}}}


def make_checked_source(**status):
    return hiedge.HiEdgeSpeedSource(SystemClock(), verify=False,
                                    references=FakeReferences(),
                                    status=FakeStatus(**status))


def test_preflight_blocks_start_when_position_is_invalid():
    source = make_checked_source(valid=False, kmh=None, label="No fix", satellites=0)
    ready, reason = source.preflight()
    assert ready is False
    assert "위치가 유효하지 않아" in reason
    assert "위성 0개" in reason


def test_preflight_blocks_start_while_moving():
    source = make_checked_source(kmh=12.0)
    ready, reason = source.preflight()
    assert ready is False
    assert "정차한 상태에서만" in reason


def test_preflight_blocks_start_when_the_device_is_full():
    source = hiedge.HiEdgeSpeedSource(SystemClock(), verify=False,
                                      references=FakeReferences(free=0),
                                      status=FakeStatus())
    ready, reason = source.preflight()
    assert ready is False
    assert "가득" in reason


def test_preflight_passes_when_stopped_with_a_valid_position():
    assert make_checked_source(kmh=0.0).preflight() == (True, "")


def test_preflight_does_not_use_a_subscription_slot():
    """반복 확인이 다른 구독자를 밀어내면 안 되므로 WebSocket을 쓰지 않는다."""
    source = make_checked_source()
    def fail(*args, **kwargs):
        raise AssertionError("preflight가 WebSocket에 접속했다")
    original = hiedge._first_speed
    hiedge._first_speed = fail
    try:
        assert source.preflight() == (True, "")
    finally:
        hiedge._first_speed = original


def test_preflight_recovers_when_the_fix_arrives():
    """fix가 잡히면 다시 물었을 때 통과한다(실험 창을 다시 열 필요가 없다)."""
    status = FakeStatus(valid=False, kmh=None, label="No fix", satellites=0)
    source = hiedge.HiEdgeSpeedSource(SystemClock(), verify=False,
                                      references=FakeReferences(), status=status)
    assert source.preflight()[0] is False
    status.valid, status.kmh, status.satellites = True, 0.0, 20
    assert source.preflight() == (True, "")


def test_unknown_speed_does_not_block_the_start():
    """INS가 초기화되지 않으면 위치해가 멀쩡해도 속도가 null이다.

    장치는 그 상태에서도 기준점 등록을 받아준다(실측). 여기서 막으면 GPS가 잡혔는데도
    시작할 수 없다. 최종 판단은 장치에 맡기고, 거절하면 그 사유를 보여준다.
    """
    source = make_checked_source(kmh=None)
    assert source.preflight() == (True, "")


def test_status_failure_is_reported_not_guessed():
    source = hiedge.HiEdgeSpeedSource(
        SystemClock(), verify=False, references=FakeReferences(),
        status=FakeStatus(error=AppError("LAN_STATUS_UNAVAILABLE", "장치 상태를 조회할 수 없습니다")))
    ready, reason = source.preflight()
    assert ready is False
    assert "조회할 수 없습니다" in reason


def test_stale_fix_is_not_treated_as_valid():
    """만료된 위치해를 현재 해로 쓰지 않는다."""
    latest = {"status": {"fix": {"solution_valid": True, "stale": True,
                                 "stale_reason": "no_fresh_solution"}}}
    valid, reason = hiedge_status.fix_state(latest)
    assert valid is False
    assert "만료" in reason


# --- 지역 서버로 소켓 경로 확인 ---

@pytest.fixture
def fake_device():
    """같은 규격으로 hello + speed를 보내는 지역 WebSocket 서버.

    실장비는 실내에서 valid:false만 보내므로, 유효 속도·변위가 흐르는 경로는 여기서
    확인한다. TLS 없이(ws://) 띄우고 소스의 URL만 바꿔 끼운다.
    """
    from websockets.sync.server import serve

    state = {"sent": 0}

    def handler(connection):
        connection.send(json.dumps({"schema": SCHEMA, "type": "hello", "stream_id": "test-stream",
                                    "next_seq": 1, "publication_hz": 50, "replay": False,
                                    "max_queue_delay_s": 0.5}))
        seq = 1
        try:
            while True:
                north = (seq - 1) * 0.5
                connection.send(json.dumps(speed_message(
                    seq, kmh=36.0, latitude=37.0, longitude=127.0,
                    offsets=[offset(east=0.0, north=north, distance=north)])))
                state["sent"] += 1
                seq += 1
                import time
                time.sleep(0.02)
        except Exception:
            return

    server = serve(handler, "127.0.0.1", 0)
    port = server.socket.getsockname()[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port, state
    server.shutdown()


def test_live_socket_delivers_samples_into_the_session(fake_device):
    """연결 → 수신 → 표본 변환 → 연결 해제가 실제 소켓으로 동작한다."""
    import time
    port, _ = fake_device
    source = hiedge.HiEdgeSpeedSource(SystemClock(), host="127.0.0.1", port=port, verify=False)
    # TLS 없이 띄운 시험 서버에 맞춰 주소만 바꾼다. 변환 경로는 그대로다.
    source.url = f"ws://127.0.0.1:{port}/ws/data"
    source.prepare()
    source.reference_id = REFERENCE_ID
    try:
        deadline = time.monotonic() + 5
        samples = ()
        while time.monotonic() < deadline:
            samples = source.read("s1", 0.0, 1, "정상")
            if samples:
                break
            time.sleep(0.05)
        assert samples, "소켓에서 표본이 들어와야 한다"
        got = {sample.channel_id: sample for sample in samples}
        assert len(got) == 6
        assert got["A.0"].value == pytest.approx(36.0)
        assert got["A.1"].value is not None, "변위가 오므로 거리도 채워진다"
        assert source.received > 0
        assert "연결됨" in source.status
    finally:
        source.close()
    assert source.connected is False


def test_restart_after_a_dropout_connects_again(fake_device):
    """단절로 끝난 뒤 다시 시작하면 새 연결을 **실제로** 기다렸다 받는다.

    지난 연결에서 센 수신 수와 남아 있던 오류를 그대로 쓰면 두 가지가 깨진다.
    - 옛 오류 때문에 장치가 멀쩡히 돌아와도 시작이 즉시 실패한다.
    - 옛 수신 수 때문에 새 연결이 되기도 전에 성공으로 치고 넘어간다.
    """
    import time
    port, _ = fake_device
    source = hiedge.HiEdgeSpeedSource(SystemClock(), host="127.0.0.1", port=port, verify=False)
    source.url = f"ws://127.0.0.1:{port}/ws/data"
    source.prepare()
    first = source.received
    assert first > 0
    source.close()
    # 실제 단절은 여기에 사유를 남긴다. 그것이 다음 시작을 막으면 안 된다.
    source.last_error = "ConnectionClosedError: 1006"
    source.prepare()
    try:
        assert source.last_error == "", "옛 오류를 들고 새 연결을 판단하지 않는다"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and source.received <= first:
            time.sleep(0.05)
        assert source.received > first, "이번 연결에서 받은 것이 있어야 한다"
    finally:
        source.close()


def test_a_mid_stream_dropout_reconnects_and_keeps_delivering():
    """끊기면 스스로 다시 붙어 수신을 이어 간다. 세션은 그 사이 기다리면 된다."""
    import time
    from websockets.sync.server import serve

    connections = {"count": 0}

    def handler(connection):
        connections["count"] += 1
        connection.send(json.dumps({"schema": SCHEMA, "type": "hello", "stream_id": "test-stream",
                                    "next_seq": 1, "publication_hz": 50}))
        for seq in range(1, 4):
            connection.send(json.dumps(speed_message(seq, kmh=36.0)))
            time.sleep(0.02)
        # 장비가 끊겼다. 이 종료는 FIFO 교체(4001)가 아니므로 다시 붙어야 한다.
        connection.close(1011, "dropped")

    server = serve(handler, "127.0.0.1", 0)
    port = server.socket.getsockname()[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        source = hiedge.HiEdgeSpeedSource(SystemClock(), host="127.0.0.1", port=port, verify=False)
        source.url = f"ws://127.0.0.1:{port}/ws/data"
        source.prepare()
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and connections["count"] < 2:
                time.sleep(0.05)
            assert connections["count"] >= 2, "끊긴 뒤 스스로 다시 붙어야 한다"
            assert source.reconnects >= 1
            assert source.evicted is False, "4001이 아닌 단절은 재접속을 멈추지 않는다"
            before = source.received
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and source.received <= before:
                time.sleep(0.05)
            assert source.received > before, "다시 붙은 뒤 수신이 이어진다"
        finally:
            source.close()
    finally:
        server.shutdown()


def test_schema_mismatch_stops_instead_of_guessing():
    """다른 규격이면 적당히 해석하지 않고 멈춘다."""
    import time
    from websockets.sync.server import serve

    def handler(connection):
        connection.send(json.dumps({"schema": "something.else.v9", "type": "hello"}))
        time.sleep(1)

    server = serve(handler, "127.0.0.1", 0)
    port = server.socket.getsockname()[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        source = hiedge.HiEdgeSpeedSource(SystemClock(), host="127.0.0.1", port=port, verify=False)
        source.url = f"ws://127.0.0.1:{port}/ws/data"
        with pytest.raises(AppError) as caught:
            source.prepare()
        assert caught.value.code in {"LAN_UNAVAILABLE", "LAN_TIMEOUT"}
        assert "규격" in source.status or source.last_error
    finally:
        server.shutdown()


def test_fifo_eviction_stops_automatic_reconnect():
    """4001로 끊기면 다시 붙지 않는다. 서로를 계속 밀어내면 둘 다 받지 못한다."""
    import time
    from websockets.sync.server import serve

    attempts = {"count": 0}

    def handler(connection):
        attempts["count"] += 1
        connection.send(json.dumps({"schema": SCHEMA, "type": "hello", "stream_id": "test-stream",
                                    "next_seq": 1, "publication_hz": 50}))
        connection.send(json.dumps(speed_message(1, kmh=36.0)))
        time.sleep(0.1)
        connection.close(hiedge.EVICTION_CLOSE_CODE, "evicted")

    server = serve(handler, "127.0.0.1", 0)
    port = server.socket.getsockname()[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        source = hiedge.HiEdgeSpeedSource(SystemClock(), host="127.0.0.1", port=port, verify=False)
        source.url = f"ws://127.0.0.1:{port}/ws/data"
        source.prepare()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not source.evicted:
            time.sleep(0.05)
        assert source.evicted, "4001을 교체로 알아본다"
        before = attempts["count"]
        time.sleep(1.5)
        assert attempts["count"] == before, "교체된 뒤에는 다시 접속하지 않는다"
        assert source.reconnects == 0
        source.close()
    finally:
        server.shutdown()
