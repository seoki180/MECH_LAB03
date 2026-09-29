"""시작 단계 동작을 별도 프로세스로 검증한다.

wx를 import하기 전에 끝나는 경로만 다루므로 GUI 없는 러너에서도 돈다. 이 검증이
필요한 이유: 배포본에서 '실행했는데 아무 반응이 없다'는 증상은 모두 GUI가 뜨기 전의
조용한 종료였다. windowed 빌드는 콘솔이 없어 안내와 예외 추적이 전부 사라진다.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def launch(home, *args, env=None):
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"), MECHLAB_HOME=str(home))
    # 개발 환경의 환경 변수가 새어 들어오면 판정이 어긋난다.
    for key in ("MECHLAB_NMEA", "MECHLAB_MAP_PACK", "MECHLAB_DATA_DIR"):
        environment.pop(key, None)
    environment.update(env or {})
    return subprocess.run([sys.executable, "-m", "experiment_app", *args],
                          capture_output=True, text=True, encoding="utf-8",
                          timeout=120, cwd=ROOT, env=environment)


def test_print_paths_uses_external_home(tmp_path):
    done = launch(tmp_path, "--print-paths")
    assert done.returncode == 0, done.stderr
    assert str(tmp_path) in done.stdout


def test_missing_explicit_nmea_reports_and_exits(tmp_path):
    """직접 지정한 파일이 없으면 조용히 다른 파일로 대체하지 않고 안내와 함께 멈춘다."""
    missing = tmp_path / "없는파일.nmea"
    done = launch(tmp_path, "--nmea", str(missing))
    assert done.returncode == 2
    log = tmp_path / "MECHLab-오류.txt"
    # 콘솔이 없는 windowed 빌드에서는 이 파일이 유일한 단서다.
    assert log.is_file()
    assert str(missing) in log.read_text(encoding="utf-8")


def test_missing_env_nmea_reports_and_exits(tmp_path):
    missing = tmp_path / "없는파일.nmea"
    done = launch(tmp_path, env={"MECHLAB_NMEA": str(missing)})
    assert done.returncode == 2
    assert str(missing) in (tmp_path / "MECHLab-오류.txt").read_text(encoding="utf-8")


def test_empty_data_folders_reach_gui_stage(tmp_path, monkeypatch):
    """NMEA와 지도 팩이 모두 없어도 GUI 단계까지 진행하고 시작 로그를 남긴다.

    배포 zip의 maps/nmea는 안내 파일만 든 빈 폴더다. 예전에는 이 상태에서 GUI 전에
    종료 코드 2로 끝나 사용자에게는 '반응 없음'으로만 보였다.
    """
    from experiment_app import bootstrap
    (tmp_path / "maps").mkdir()
    (tmp_path / "nmea").mkdir()
    monkeypatch.setenv("MECHLAB_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["MECHLab"])
    original = bootstrap.write_startup_log
    reached = []

    def spy(*args):
        # 시작 로그까지 도달했으면 그 다음 줄이 wx import다. GUI 자체는 이 테스트
        # 대상이 아니므로 여기서 멈춘다.
        original(*args)
        reached.append(args)
        raise SystemExit(0)

    monkeypatch.setattr(bootstrap, "write_startup_log", spy)
    try:
        bootstrap.run()
    except SystemExit as stop:
        assert stop.code == 0
    assert reached, "GUI 단계 전에 종료됐다"
    body = (tmp_path / "MECHLab-시작로그.txt").read_text(encoding="utf-8")
    assert "내장 데모 센서" in body
    assert "지도 타일팩이 없어" in body
