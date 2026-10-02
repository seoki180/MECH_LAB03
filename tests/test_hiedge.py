"""HI-EDGE LAN WebSocket 소스 검증.

실장비가 실내에서 fix를 얻지 못해 ``valid:false`` 만 보내므로, 유효 속도와 좌표가
들어오는 경로는 같은 규격을 흉내내는 지역 서버로 확인한다. 실장비 연결 자체는
수동 검증(``python -m tools.lan_probe``)으로 확인했다.

확인 범위:
- 유효 메시지가 6채널 표본으로 바뀐다.
- ``valid:false`` 는 값이 None이고 0으로 채우지 않는다.
- 좌표가 없으면 거리·방향·누적거리를 만들어내지 않는다.
- 좌표가 오면 기준점은 **세션 시작 후 첫 유효 좌표**이고, 세션이 바뀌면 다시 잡는다.
- 정지 중 좌표 흔들림이 누적거리를 늘리지 않는다.
- 규격이 다르거나 연결이 안 되면 구조화된 오류 코드로 거부한다.
"""

import json
import ssl
import threading

import pytest

from experiment_app.domain.test_definition import AppError
from experiment_app.infrastructure import hiedge
from experiment_app.infrastructure.sources import SystemClock

pytest.importorskip("websockets")


SCHEMA = hiedge.SCHEMA


def speed_message(seq, *, valid=True, kmh=36.0, latitude=None, longitude=None, gap=False):
    message = {"schema": SCHEMA, "type": "speed", "stream_id": "test-stream", "seq": seq,
               "publication_hz": 50, "output_time_utc": f"2026-10-02T05:00:{seq % 60:02d}.000000Z",
               "output_monotonic_s": float(seq) * 0.02, "source_interval_s": 0.02,
               "source_gap": gap, "valid": valid, "input_connected": True, "input_age_s": 0.01,
               "estimate": None}
    if valid:
        message["estimate"] = {"speed_kmh": kmh, "speed_mps": kmh / 3.6,
                               "measurement_kind": "measurement"}
    if latitude is not None:
        message["latitude"], message["longitude"] = latitude, longitude
    return message


# --- 메시지 → 표본 변환 (소켓 없이) ---

def make_source():
    """소켓을 열지 않고 변환만 확인하기 위한 소스."""
    return hiedge.HiEdgeSpeedSource(SystemClock(), verify=False)


def values(source, session_id, message):
    source.submit(message)
    samples = source.read(session_id, 0.0, 1, "정상")
    return {sample.channel_id: sample for sample in samples}


def test_valid_speed_without_coordinates_fills_only_speed_channels():
    source = make_source()
    got = values(source, "s1", speed_message(1, kmh=36.0))
    assert len(got) == 6, "채널 수는 좌표 유무와 무관하게 6개다"
    assert got["A.0"].value == pytest.approx(36.0)
    assert got["C.0"].value == pytest.approx(36.0)
    # 좌표가 없으면 거리·방향을 지어내지 않는다.
    for channel in ("A.1", "B.0", "B.1", "C.1"):
        assert got[channel].value is None, f"{channel}은 좌표 없이 계산할 수 없다"
    assert "좌표 없음" in got["A.0"].quality


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


def test_coordinates_produce_distance_from_the_session_start():
    """기준점은 세션 시작 후 첫 유효 좌표다."""
    source = make_source()
    first = values(source, "s1", speed_message(1, kmh=36.0, latitude=37.0, longitude=127.0))
    assert first["A.1"].value == pytest.approx(0.0, abs=0.01), "첫 좌표가 기준점이므로 거리는 0"
    assert first["B.0"].value == pytest.approx(0.0, abs=0.01)
    # 북쪽으로 약 111 m 이동.
    second = values(source, "s1", speed_message(2, kmh=36.0, latitude=37.001, longitude=127.0))
    assert second["B.1"].value == pytest.approx(111.2, abs=1.0), "종방향(북)"
    assert second["B.0"].value == pytest.approx(0.0, abs=0.5), "횡방향은 움직이지 않았다"
    assert second["A.1"].value == pytest.approx(111.2, abs=1.0)
    assert second["C.1"].value == pytest.approx(111.2, abs=1.0), "누적거리"


def test_origin_is_reset_for_a_new_session():
    """이전 세션의 기준점이 다음 세션으로 넘어가지 않는다."""
    source = make_source()
    values(source, "s1", speed_message(1, latitude=37.0, longitude=127.0))
    values(source, "s1", speed_message(2, latitude=37.001, longitude=127.0))
    fresh = values(source, "s2", speed_message(3, latitude=37.001, longitude=127.0))
    assert fresh["A.1"].value == pytest.approx(0.0, abs=0.01), "새 세션의 첫 좌표가 새 기준점이다"
    assert fresh["C.1"].value == pytest.approx(0.0, abs=0.01), "누적거리도 다시 센다"


def test_stationary_jitter_does_not_accumulate_distance():
    """정지 중 좌표가 흔들려도 누적거리가 늘지 않는다."""
    source = make_source()
    values(source, "s1", speed_message(1, kmh=0.0, latitude=37.0, longitude=127.0))
    # 속도가 판정값 미만이므로 좌표가 흔들려도 더하지 않는다.
    got = values(source, "s1", speed_message(2, kmh=0.2, latitude=37.00001, longitude=127.00001))
    assert got["C.1"].value == pytest.approx(0.0, abs=0.01)
    # 직선거리는 기준점 대비 실제 좌표 차이이므로 0이 아니다. 둘은 다른 값이다.
    assert got["A.1"].value > 0


def test_coordinate_gap_does_not_bridge_travelled_distance():
    """좌표가 빠진 구간의 경로는 알 수 없으므로 이어 붙이지 않는다."""
    source = make_source()
    values(source, "s1", speed_message(1, kmh=36.0, latitude=37.0, longitude=127.0))
    values(source, "s1", speed_message(2, kmh=36.0, latitude=37.001, longitude=127.0))
    values(source, "s1", speed_message(3, kmh=36.0))  # 좌표 없음
    after = values(source, "s1", speed_message(4, kmh=36.0, latitude=37.002, longitude=127.0))
    # 끊기기 전 111 m만 남고, 빈 구간은 더해지지 않는다.
    assert after["C.1"].value == pytest.approx(111.2, abs=1.0)
    # 기준점은 유지되므로 직선거리는 계속 늘어난다.
    assert after["A.1"].value == pytest.approx(222.4, abs=2.0)


def test_source_gap_is_visible_in_quality():
    source = make_source()
    got = values(source, "s1", speed_message(1, gap=True))
    assert "단절" in got["A.0"].quality


def test_gps_source_reports_no_fix_when_coordinates_are_absent():
    source = make_source()
    gps = hiedge.HiEdgeGpsSource(source)
    assert gps.read("s1", 0.0, "정상") is None, "아직 받은 것이 없으면 None"
    values(source, "s1", speed_message(1, kmh=36.0))
    fix = gps.read("s1", 0.0, "정상")
    assert fix is not None and fix.latitude is None, "좌표 없음을 fix 없음으로 알린다"
    values(source, "s1", speed_message(2, kmh=36.0, latitude=37.0, longitude=127.0))
    fix = gps.read("s1", 0.0, "정상")
    assert fix.latitude == pytest.approx(37.0)


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


# --- 지역 서버로 소켓 경로 확인 ---

@pytest.fixture
def fake_device():
    """같은 규격으로 hello + speed를 보내는 지역 WebSocket 서버.

    실장비는 실내에서 valid:false만 보내므로, 유효 속도·좌표가 흐르는 경로는
    여기서 확인한다. TLS 없이(ws://) 띄우고 소스의 URL만 바꿔 끼운다.
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
                latitude = 37.0 + (seq - 1) * 0.0001
                connection.send(json.dumps(speed_message(seq, kmh=36.0, latitude=latitude,
                                                         longitude=127.0)))
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
        assert got["A.1"].value is not None, "좌표가 오므로 거리도 계산된다"
        assert source.received > 0
        assert "연결됨" in source.status
    finally:
        source.close()
    assert source.connected is False


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
