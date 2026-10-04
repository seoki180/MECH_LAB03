import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from experiment_app.demo.fixtures import schemas, CHANNELS, BASIC_FIELDS, SCENARIOS
from experiment_app.infrastructure.test_folders import FolderTestRepository
from experiment_app.infrastructure.results import LocalResultRepository
from experiment_app.infrastructure.sources import FakeSensorSource, FakeGpsSource, SystemClock
from experiment_app.infrastructure.nmea import NMEA_CHANNELS, NmeaReplay, NmeaSensorSource, NmeaGpsSource
from experiment_app.infrastructure.recording import JsonlRecorder
from experiment_app.infrastructure.tiles import MapPackSet
from experiment_app.infrastructure.settings_store import SettingsStore
from experiment_app.infrastructure import hiedge, http_robot
from experiment_app.application.test_service import TestService
from experiment_app.application.robot_service import RobotService
from experiment_app.infrastructure.robot import DemoRobotTransport
from experiment_app.application.session_service import SessionService
from experiment_app.application.telemetry_service import TelemetryService
from experiment_app.application.result_service import ResultService
from experiment_app.application.copy_service import CopyService
from experiment_app.application.analysis_service import AnalysisService
from experiment_app.presentation.main_presenter import MainPresenter
from experiment_app.presentation.experiment_presenter import ExperimentPresenter


def _robot_transport(clock, override, url, settings):
    """쓸 로봇 전송을 고른다.

    우선순위는 호출부 주입(테스트) > 명령줄 ``--robot-url`` > 저장된 설정이다.
    어디에도 주소가 없으면 데모 전송을 쓴다. 실장비 주소를 추측해 넣지 않는다 —
    없는 주소로 붙으려다 실패하면 사용자에게는 원인 없는 시작 실패로 보인다.
    """
    if override is not None:
        return override
    if url:
        return http_robot.HttpRobotTransport(clock, url=url)
    if settings.get("enabled") and settings.get("host"):
        return http_robot.HttpRobotTransport(clock, host=settings["host"],
                                             port=settings.get("port", http_robot.DEFAULT_PORT))
    return DemoRobotTransport(clock)


def build_services(data_dir, duration=None, nmea_path=None, robot_transport=None, tests_dir=None,
                   robot_url=None):
    """duration은 측정 시간 상한(초)이다. None이면 중지할 때까지 계속 받는다.

    실제 운용은 None이다. 값을 주는 곳은 테스트와 스모크뿐이며, 시험의 길이를 정하는
    설정이 아니라 자동 검증을 짧게 끝내기 위한 장치다.

    ``robot_url``/저장된 로봇 설정이 있으면 HTTP 전송을, 없으면 데모 전송을 쓴다.
    규격은 docs/ROBOT_HTTP_API.md다.
    """
    data_dir = Path(data_dir)
    # 시험 정의는 실행 파일 밖 test/ 폴더에서 직접 관리한다. 실행 시에는 bootstrap.run()이
    # paths.tests_dir()을 넘기고, 지정이 없으면 저장 폴더 아래를 쓴다.
    tests_dir = Path(tests_dir) if tests_dir else data_dir / "test"
    # 시험은 사용자가 만들거나 폴더를 가져와서 생긴다. 데모 시험을 미리 넣지 않는다.
    repository = FolderTestRepository(tests_dir, legacy_path=data_dir / "tests.json")
    test_service = TestService(repository, schemas)
    telemetry = TelemetryService()
    clock = SystemClock()
    results = ResultService(LocalResultRepository(data_dir / "recordings"))
    def nmea_sources(path):
        replay = NmeaReplay(path, clock)
        return NMEA_CHANNELS, NmeaSensorSource(replay), NmeaGpsSource(replay)

    channels, sensors, gps = nmea_sources(nmea_path) if nmea_path else (
        CHANNELS, FakeSensorSource(CHANNELS, clock), FakeGpsSource(clock))

    def lan_sources(host, port, verify=True, certificate=None):
        """HI-EDGE LAN 스트림 소스. import를 여기서 하는 이유는 websockets가 없는
        환경에서도 앱이 시작되어야 하고, 부재는 선택 시점에 알려야 하기 때문이다."""
        from experiment_app.infrastructure.hiedge import build_sources
        return build_sources(clock, host, port, certificate, verify)

    settings_store = SettingsStore(data_dir / "settings.json")
    robot_settings = settings_store.robot({"host": http_robot.DEFAULT_HOST,
                                           "port": http_robot.DEFAULT_PORT, "enabled": False})

    def robot_sources(host, port, enabled=True):
        """주소로 로봇 전송을 만든다. 설정 화면의 '주소 적용'이 쓰는 경로다.

        켜지 않았으면 데모 전송을 준다. 주소만 남기고 실제 로봇에는 보내지 않는
        상태이며, UI는 demo 플래그를 보고 '데모'로 표시한다.
        """
        if not enabled:
            return DemoRobotTransport(clock)
        return http_robot.HttpRobotTransport(clock, host=host,
                                             port=port or http_robot.DEFAULT_PORT)

    sessions = SessionService(repository, channels, sensors, gps,
                              lambda: JsonlRecorder(data_dir / "recordings"), telemetry, results, clock, duration,
                              robot=RobotService(_robot_transport(clock, robot_transport, robot_url,
                                                                 robot_settings), clock))
    main_presenter = MainPresenter(test_service)
    # 결과 그래프는 기록 파일을 다시 읽어 그린다. 채널은 sessions에서 읽으므로
    # 장치 탭에서 NMEA 파일을 바꿔도 라벨·단위가 화면과 어긋나지 않는다.
    analysis = AnalysisService(results, sessions)
    experiment_presenter = ExperimentPresenter(sessions, telemetry, CopyService(clock), clock,
                                               nmea_sources, nmea_path, analysis, lan_sources,
                                               settings_store=settings_store,
                                               lan_defaults={"host": hiedge.DEFAULT_HOST,
                                                             "port": hiedge.DEFAULT_PORT,
                                                             "verify": True, "certificate": None},
                                               robot_sources=robot_sources,
                                               robot_defaults={"host": http_robot.DEFAULT_HOST,
                                                               "port": http_robot.DEFAULT_PORT,
                                                               "enabled": False})
    # 명령줄로 주소를 준 경우 화면에도 그 주소가 보여야 한다. 실제 접속 주소와
    # 설정 화면의 값이 어긋나면 사용자가 어디로 보내는지 알 수 없다.
    transport = sessions.robot.transport
    if robot_url and not getattr(transport, "demo", True):
        experiment_presenter.robot_settings = {"host": transport.host, "port": transport.port,
                                               "enabled": True}
    return main_presenter, experiment_presenter


def safe_print(body):
    """콘솔이 없거나 한글을 못 쓰는 환경에서도 죽지 않고 출력한다.

    Windows에서 두 가지가 문제가 된다.
    - windowed 빌드(console=False)는 sys.stdout이 None이라 print가 AttributeError를 낸다.
    - 콘솔이 있어도 기본 코드 페이지가 cp1252면 한글에서 UnicodeEncodeError가 난다.

    진단 출력 실패가 앱을 멈추게 해서는 안 되므로 어떤 경우에도 예외를 올리지 않는다.
    출력이 실제로 어딘가에 도달했는지 돌려준다. 호출부는 이 값으로 대화상자 같은
    대체 수단이 필요한지 판단한다.
    """
    stream = sys.stdout
    if stream is None:
        return False
    try:
        print(body, file=stream)
        return True
    except (UnicodeEncodeError, AttributeError, ValueError, OSError):
        pass
    # 콘솔이 인코딩하지 못하는 글자는 버리고 나머지라도 보여준다.
    try:
        buffer = getattr(stream, "buffer", None)
        encoding = getattr(stream, "encoding", None) or "utf-8"
        if buffer is not None:
            buffer.write((body + "\n").encode(encoding, errors="replace"))
            buffer.flush()
        else:
            print(body.encode(encoding, errors="replace").decode(encoding, errors="replace"),
                  file=stream)
        return True
    except Exception:
        # 출력은 부가 기능이다. 여기서 실패해도 --report 파일과 종료 코드는 유효하다.
        return False


def emit(body, report=None):
    """진단 출력을 표준 출력과, 지정된 경우 파일에 함께 쓴다.

    Windows GUI 빌드(console=False)는 콘솔이 없어 print가 아무 데도 보이지 않는다.
    CI와 현장 점검은 --report로 파일을 받아 결과를 확인한다.
    """
    # 파일을 먼저 쓴다. 콘솔 출력이 실패해도 진단 결과는 남아야 한다.
    if report is not None:
        report = Path(report)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(body + "\n", encoding="utf-8")
    safe_print(body)


def alert(body, title="MECHLab"):
    """시작 실패를 사용자가 볼 수 있는 곳에 남긴다.

    Windows GUI 빌드(console=False)는 stdout/stderr가 없다. 그래서 종료 안내나
    예외 추적이 전부 사라지고, 사용자에게는 '실행했는데 아무 반응이 없음'으로만
    보인다.

    1) 실행 파일 옆 오류 로그 파일 — 항상 남기므로 현장 점검에 쓴다.
    2) 표준 출력 — 콘솔에서 실행했을 때.
    3) 네이티브 메시지 박스 — 위 출력이 어디에도 도달하지 못했을 때만.

    대화상자는 모달이라 누를 사람이 없으면 프로세스가 영원히 멈춘다. CI와 스크립트
    실행은 콘솔이 있으므로 2)에서 끝나고 여기까지 오지 않는다.
    """
    from experiment_app import paths
    log = None
    try:
        log = paths.external_root() / "MECHLab-오류.txt"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(body + "\n", encoding="utf-8")
    except OSError:
        log = None
    if safe_print(body):
        return
    shown = body if log is None else f"{body}\n\n자세한 내용: {log}"
    try:
        if sys.platform == "win32":
            # wx가 아직 초기화되지 않았거나 죽은 뒤일 수 있으므로 wx를 거치지 않고
            # user32를 직접 부른다.
            import ctypes
            # MB_OK | MB_ICONERROR | MB_SETFOREGROUND
            ctypes.windll.user32.MessageBoxW(None, shown, title, 0x10 | 0x10000)
            return
        # wx.App을 새로 만들면 이벤트 루프 없이 모달이 떠 멈출 수 있다. 이미 App이
        # 있을 때만(=GUI 시작 후 실패) 창으로 알린다.
        import wx
        if wx.GetApp() is not None:
            wx.MessageBox(shown, title, wx.OK | wx.ICON_ERROR)
    except Exception:
        # 안내 표시 실패가 종료 코드와 로그 파일을 무효화해서는 안 된다.
        pass


def write_startup_log(notices, data_dir, map_pack, nmea_path, tests_dir=None):
    """시작 경로와 안내를 실행 파일 옆 로그에 남긴다.

    windowed 빌드는 콘솔이 없어 진단 출력이 전부 사라진다. 창이 뜨지 않았을 때
    이 파일이 유일한 단서다. 로그 작성 실패가 실행을 막아서는 안 된다.
    """
    from experiment_app import paths
    lines = [f"MECHLab 시작 {datetime.now().astimezone().isoformat(timespec='seconds')}"]
    lines += [f"{key}: {value}" for key, value in paths.describe().items()]
    lines += [f"사용할 data_dir: {data_dir}", f"사용할 map_pack: {map_pack}",
              f"사용할 tests_dir: {tests_dir}",
              f"사용할 nmea: {nmea_path or '없음 (내장 데모 센서)'}"]
    lines += notices
    try:
        target = paths.external_root() / "MECHLab-시작로그.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def main():
    """진입점. 어떤 예외도 화면에 보이는 안내로 바꿔서 끝낸다."""
    try:
        return run()
    except SystemExit:
        raise
    except BaseException:
        import traceback
        alert("프로그램을 시작하지 못했습니다.\n\n" + traceback.format_exc())
        raise SystemExit(1)


def run():
    from experiment_app import paths
    parser = argparse.ArgumentParser(description="MECHLab 데모 실험 관리")
    parser.add_argument("--data-dir", type=Path, default=None,
                        help=f"기록·결과 저장 폴더. 기본값은 실행 파일 옆 .mechlab ({paths.DATA_DIR_ENV} 환경 변수로도 지정)")
    parser.add_argument("--tests-dir", type=Path, default=None,
                        help="시험 폴더. 실행 파일 밖에 두며 기본값은 실행 파일 옆 test "
                             f"({paths.TESTS_DIR_ENV} 환경 변수로도 지정). "
                             "구조는 test/<시험목록>/<시험>.json 과 같은 이름의 .csv 시나리오다.")
    parser.add_argument("--map-pack", type=Path, default=None,
                        help="오프라인 지도 타일팩(.mbtiles) 디렉터리. 실행 파일 밖에 두며 "
                             f"기본값은 실행 파일 옆 maps ({paths.MAP_PACK_ENV} 환경 변수로도 지정). "
                             "없으면 지도 배경 없이 실행한다.")
    parser.add_argument("--nmea", type=Path, default=None,
                        help=f"재생할 INSPVAXA .nmea 파일. 기본값은 실행 파일 옆 nmea 폴더의 첫 파일 ({paths.NMEA_ENV} 환경 변수로도 지정)")
    parser.add_argument("--robot-url", default=None,
                        help="로봇 HTTP 주소(예: http://192.0.2.10:8080). 지정하면 실제 로봇으로 "
                             "시험 설정·시나리오·시작 신호를 보낸다. 없으면 저장된 설정을 쓰고, "
                             "그것도 없으면 데모 로봇으로 동작한다. 규격은 docs/ROBOT_HTTP_API.md")
    parser.add_argument("--print-paths", action="store_true",
                        help="해석된 경로를 출력하고 끝낸다. 배포 후 자료 폴더 확인용이다.")
    parser.add_argument("--self-check", action="store_true",
                        help="번들 자원과 외부 자료 폴더를 점검하고 끝낸다. 실패 시 0이 아닌 값을 돌려준다.")
    parser.add_argument("--report", type=Path, default=None,
                        help="--print-paths/--self-check 결과를 이 파일에도 쓴다. "
                             "Windows GUI 빌드는 콘솔이 없어 표준 출력이 보이지 않으므로 필요하다.")
    args = parser.parse_args()
    # 우선순위: 명령줄 인자 > 환경 변수 > 실행 파일 옆 기본 폴더.
    data_dir = args.data_dir or paths.data_dir()
    tests_dir = args.tests_dir or paths.tests_dir()
    map_pack = args.map_pack or paths.map_pack_dir()
    nmea_path = args.nmea or paths.default_nmea()
    if args.print_paths:
        lines = [f"{key}: {value}" for key, value in paths.describe().items()]
        lines += [f"사용할 data_dir: {data_dir}", f"사용할 tests_dir: {tests_dir}",
                  f"사용할 map_pack: {map_pack}",
                  f"사용할 nmea: {nmea_path}"]
        emit("\n".join(lines), args.report)
        return
    if args.self_check:
        from experiment_app.self_check import run
        raise SystemExit(run(args.report))
    explicit_nmea = args.nmea or os.environ.get(paths.NMEA_ENV)
    if explicit_nmea and not Path(explicit_nmea).is_file():
        # 사용자가 직접 지정한 파일이 없으면 조용히 다른 것으로 바꾸지 않는다.
        # 여기서 막지 않으면 GUI가 뜬 뒤 재생 단계에서야 실패한다.
        alert(f"지정한 NMEA 파일이 없습니다:\n{explicit_nmea}")
        raise SystemExit(2)
    notices = []
    if nmea_path is None:
        # 로그가 없다고 앱이 실행되지 않으면 사용자에게는 '아무 반응 없음'으로 보인다.
        # 지도 팩이 없을 때와 같은 판단으로, 내장 데모 센서로 대체해 실행한다.
        notices.append(f"NMEA 로그를 찾지 못해 내장 데모 센서로 실행합니다. "
                       f"{paths.external_root() / 'nmea'} 폴더에 .nmea 파일을 넣으면 재생합니다.")
    # Load persisted state before the GUI event loop; all subsequent disk I/O is asynchronous.
    main_presenter, experiment_presenter = build_services(data_dir, nmea_path=nmea_path,
                                                          tests_dir=tests_dir,
                                                          robot_url=args.robot_url)
    for path, error in main_presenter.service.repository.load_errors:
        notices.append(f"시험을 읽지 못했습니다: {path.parent.name} ({error})")
    tiles, failures = MapPackSet.load(map_pack)
    for path, error in failures:
        notices.append(f"타일팩을 열지 못했습니다: {path} ({error})")
    if not tiles.available:
        notices.append(f"지도 타일팩이 없어 배경 없이 실행합니다. 찾은 위치: {map_pack}")
    # windowed 빌드에서는 콘솔이 없어 위 안내가 어디에도 보이지 않는다. 실행 파일 옆
    # 로그 파일에 남겨 '실행했는데 아무 반응이 없다'를 확인할 수 있게 한다.
    for line in notices:
        safe_print(line)
    write_startup_log(notices, data_dir, map_pack, nmea_path, tests_dir)
    import wx
    from experiment_app.ui.adapters.clipboard import WxClipboard
    from experiment_app.ui.frames.main_frame import MainFrame
    from experiment_app.ui.theme import load_fonts
    app = wx.App(False)
    load_fonts()
    frame = MainFrame(main_presenter, experiment_presenter, WxClipboard(), tiles,
                      BASIC_FIELDS, SCENARIOS)
    frame.Show()
    app.MainLoop()
    tiles.close()


if __name__ == "__main__":
    main()
