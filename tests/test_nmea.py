from pathlib import Path
import math

import pytest

from experiment_app.bootstrap import build_services
from experiment_app.domain.session import State
from experiment_app.domain.telemetry import displacement
from experiment_app.infrastructure.nmea import NmeaReplay, parse_inspvaxa
from experiment_app.infrastructure.sources import SystemClock


def line(time, quality, lat, lon, north=0, east=0, status="INS_SOLUTION_GOOD"):
    return (f"#INSPVAXA,COM1,0,0,FINE,2430,{time},0,0,0;"
            f"{status},{quality},{lat},{lon},12,0,{north},{east},0,0,0,0*00000000\n")


def test_parse_rtk_fix_and_missing_fix():
    fixed = parse_inspvaxa(line(100, "INS_RTKFIXED", 37.2, 126.7, 3, 4))
    assert fixed.quality == "INS_RTKFIXED"
    assert (fixed.latitude, fixed.longitude, fixed.north_velocity, fixed.east_velocity) == (37.2, 126.7, 3, 4)
    missing = parse_inspvaxa(line(101, "NONE", 0, 0, status="INS_ALIGNING"))
    assert missing.latitude is None and missing.quality == "fix 없음"
    assert parse_inspvaxa("$GPGGA,unused") is None


def test_replay_uses_first_valid_start_and_recorded_velocity(tmp_path):
    path = tmp_path / "trip.nmea"
    # NMEA는 ASCII 프로토콜이고 NmeaReplay도 ascii로 읽는다. 쓸 때 인코딩을
    # 생략하면 Windows에서 cp1252가 되므로 호환되는 ascii를 명시한다.
    path.write_text(line(100, "NONE", 0, 0, status="INS_ALIGNING")
                    + line(101, "INS_RTKFIXED", 37.2, 126.7, 3, 4)
                    + line(102, "INS_RTKFIXED", 37.201, 126.702, 6, 8),
                    encoding="ascii")
    replay = NmeaReplay(path, SystemClock())
    replay.prepare()
    assert replay.sensor_samples("s", 0, 1, "정상")[0].value is None
    first = replay.sensor_samples("s", 1, 2, "정상")
    assert first[0].value == pytest.approx(18)  # 3-4-5 m/s -> km/h
    assert first[1].value == 0
    later = replay.sensor_samples("s", 2, 3, "정상")
    east, north, distance = displacement(37.2, 126.7, 37.201, 126.702)
    assert [later[i].value for i in (1, 2, 3)] == pytest.approx([distance, east, north])
    assert later[0].value == pytest.approx(36)
    assert later[5].value == 0
    assert replay.gps_fix("s", 2, "정상").fix_quality == "NMEA · INS_RTKFIXED"


def test_supplied_file_drives_session(tmp_path):
    # 저장소에 포함된 픽스처를 쓴다. 원본 로그(asset/ 아래 2.1MB)는 참고 자료라
    # 저장소에 올리지 않으므로, glob으로 찾으면 CI에서 StopIteration으로 실패한다.
    path = Path(__file__).resolve().parent / "data" / "10km_log_head.nmea"
    main, experiment = build_services(tmp_path, duration=0.12, nmea_path=path)
    try:
        definition = main.service.repository.list()[0]
        session = experiment.sessions.prepare(definition.id, definition.revision)
        assert experiment.sessions.start(session.session_id)
        experiment.sessions.worker.join(4)
        assert experiment.sessions.view().state == State.COMPLETED
        snapshot = experiment.telemetry.snapshot()
        assert snapshot.start_gps.latitude == pytest.approx(37.24584803403)
        assert snapshot.gps.fix_quality == "NMEA · INS_RTKFIXED"
        assert snapshot.movement[2] >= 0 and math.isfinite(snapshot.movement[2])
        assert {sample.unit for sample in snapshot.samples} == {"km/h", "m"}
    finally:
        main.dispose()
