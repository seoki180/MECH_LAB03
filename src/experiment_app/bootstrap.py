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


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="MECHLab 데모 실험 관리")
    parser.add_argument("--data-dir", type=Path, default=root / ".mechlab")
    parser.add_argument("--map-pack", type=Path, default=root / "asset" / "maps",
                        help="오프라인 지도 타일팩(.mbtiles) 디렉터리. 없으면 지도 배경 없이 실행한다.")
    parser.add_argument("--nmea", type=Path, default=next(root.glob("asset/**/10km_log.nmea"), None),
                        help="재생할 INSPVAXA .nmea 파일")
    args = parser.parse_args()
    if args.nmea is None:
        parser.error("기본 NMEA 파일을 찾지 못했습니다. --nmea <파일>을 지정하세요.")
    # Load persisted state before the GUI event loop; all subsequent disk I/O is asynchronous.
    main_presenter, experiment_presenter = build_services(args.data_dir, nmea_path=args.nmea)
    tiles, failures = MapPackSet.load(args.map_pack)
    for path, error in failures:
        print(f"타일팩을 열지 못했습니다: {path} ({error})")
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
