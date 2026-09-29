import argparse
from pathlib import Path
from experiment_app.demo.fixtures import fixtures, schemas, CHANNELS, BASIC_FIELDS, STEP_FIELDS, SCENARIOS
from experiment_app.infrastructure.repositories import LocalTestRepository
from experiment_app.infrastructure.results import LocalResultRepository
from experiment_app.infrastructure.sources import FakeSensorSource, FakeGpsSource, SystemClock
from experiment_app.infrastructure.nmea import NMEA_CHANNELS, NmeaReplay, NmeaSensorSource, NmeaGpsSource
from experiment_app.infrastructure.recording import JsonlRecorder
from experiment_app.infrastructure.tiles import MapPackSet
from experiment_app.application.test_service import TestService
from experiment_app.application.robot_service import RobotService
from experiment_app.infrastructure.robot import DemoRobotTransport
from experiment_app.application.session_service import SessionService
from experiment_app.application.telemetry_service import TelemetryService
from experiment_app.application.result_service import ResultService
from experiment_app.application.copy_service import CopyService
from experiment_app.presentation.main_presenter import MainPresenter
from experiment_app.presentation.experiment_presenter import ExperimentPresenter


def build_services(data_dir, duration=30, nmea_path=None, robot_transport=None):
    data_dir = Path(data_dir)
    groups, definitions = fixtures()
    repository = LocalTestRepository(data_dir / "tests.json", groups, definitions)
    test_service = TestService(repository, schemas)
    telemetry = TelemetryService()
    clock = SystemClock()
    results = ResultService(LocalResultRepository(data_dir / "recordings"))
    def nmea_sources(path):
        replay = NmeaReplay(path, clock)
        return NMEA_CHANNELS, NmeaSensorSource(replay), NmeaGpsSource(replay)

    channels, sensors, gps = nmea_sources(nmea_path) if nmea_path else (
        CHANNELS, FakeSensorSource(CHANNELS, clock), FakeGpsSource(clock))
    sessions = SessionService(repository, channels, sensors, gps,
                              lambda: JsonlRecorder(data_dir / "recordings"), telemetry, results, clock, duration,
                              robot=RobotService(robot_transport if robot_transport is not None else DemoRobotTransport(clock), clock))
    main_presenter = MainPresenter(test_service, definitions[0])
    experiment_presenter = ExperimentPresenter(sessions, telemetry, CopyService(clock), clock,
                                               nmea_sources, nmea_path)
    return main_presenter, experiment_presenter


def emit(body, report=None):
    """진단 출력을 표준 출력과, 지정된 경우 파일에 함께 쓴다.

    Windows GUI 빌드(console=False)는 콘솔이 없어 print가 아무 데도 보이지 않는다.
    CI와 현장 점검은 --report로 파일을 받아 결과를 확인한다.
    """
    print(body)
    if report is not None:
        report = Path(report)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(body + "\n", encoding="utf-8")


def main():
    from experiment_app import paths
    parser = argparse.ArgumentParser(description="MECHLab 데모 실험 관리")
    parser.add_argument("--data-dir", type=Path, default=None,
                        help=f"저장 폴더. 기본값은 실행 파일 옆 .mechlab ({paths.DATA_DIR_ENV} 환경 변수로도 지정)")
    parser.add_argument("--map-pack", type=Path, default=None,
                        help="오프라인 지도 타일팩(.mbtiles) 디렉터리. 실행 파일 밖에 두며 "
                             f"기본값은 실행 파일 옆 maps ({paths.MAP_PACK_ENV} 환경 변수로도 지정). "
                             "없으면 지도 배경 없이 실행한다.")
    parser.add_argument("--nmea", type=Path, default=None,
                        help=f"재생할 INSPVAXA .nmea 파일. 기본값은 실행 파일 옆 nmea 폴더의 첫 파일 ({paths.NMEA_ENV} 환경 변수로도 지정)")
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
    map_pack = args.map_pack or paths.map_pack_dir()
    nmea_path = args.nmea or paths.default_nmea()
    if args.print_paths:
        lines = [f"{key}: {value}" for key, value in paths.describe().items()]
        lines += [f"사용할 data_dir: {data_dir}", f"사용할 map_pack: {map_pack}",
                  f"사용할 nmea: {nmea_path}"]
        emit("\n".join(lines), args.report)
        return
    if args.self_check:
        from experiment_app.self_check import run
        raise SystemExit(run(args.report))
    if nmea_path is None:
        parser.error(f"재생할 NMEA 파일을 찾지 못했습니다. {paths.external_root() / 'nmea'} 폴더에 .nmea 파일을 넣거나 "
                     "--nmea <파일>을 지정하세요.")
    # Load persisted state before the GUI event loop; all subsequent disk I/O is asynchronous.
    main_presenter, experiment_presenter = build_services(data_dir, nmea_path=nmea_path)
    tiles, failures = MapPackSet.load(map_pack)
    for path, error in failures:
        print(f"타일팩을 열지 못했습니다: {path} ({error})")
    if not tiles.available:
        print(f"지도 타일팩이 없어 배경 없이 실행합니다. 찾은 위치: {map_pack}")
    import wx
    from experiment_app.ui.adapters.clipboard import WxClipboard
    from experiment_app.ui.frames.main_frame import MainFrame
    from experiment_app.ui.theme import load_fonts
    app = wx.App(False)
    load_fonts()
    frame = MainFrame(main_presenter, experiment_presenter, WxClipboard(), tiles,
                      (BASIC_FIELDS, STEP_FIELDS), SCENARIOS)
    frame.Show()
    app.MainLoop()
    tiles.close()


if __name__ == "__main__":
    main()
