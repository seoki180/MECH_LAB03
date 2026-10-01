"""시험 상세 화면 상단 위젯의 실제 좌표를 찍어 버튼 배치를 확인한다.

이 환경에서는 화면 캡처가 검게 나와 PNG 대신 좌표로 확인한다.
판정은 gui_smoke.py가 하고, 이 스크립트는 사람이 배치를 볼 때 쓰는 보조 도구다.
"""
import sys
from pathlib import Path

import wx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from experiment_app.bootstrap import build_services  # noqa: E402
from experiment_app.demo.fixtures import BASIC_FIELDS, STEP_FIELDS, SCENARIOS  # noqa: E402
from experiment_app.ui.frames.main_frame import MainFrame  # noqa: E402
from experiment_app.ui.adapters.clipboard import WxClipboard  # noqa: E402
from experiment_app.infrastructure.tiles import MapPackSet  # noqa: E402
from sample_tests import seed  # noqa: E402

OUT = ROOT / "artifacts"
OUT.mkdir(exist_ok=True)

app = wx.App(False)
main, experiment = build_services(OUT / "shot-data", tests_dir=OUT / "shot-test", duration=10)
# 앱은 빈 상태로 시작한다. 배치를 보려면 시험이 필요하므로 보조 자료를 넣는다.
seed(main.service.repository)
main.select(main.service.repository.list()[0].id)
tiles, _ = MapPackSet.load(OUT / "shot-maps")
frame = MainFrame(main, experiment, WxClipboard(), tiles, (BASIC_FIELDS, STEP_FIELDS), SCENARIOS)
frame.SetClientSize(frame.FromDIP(wx.Size(1280, 800)))
frame.Show()
frame.Layout()


def dump():
    details = frame.tests.details
    origin = details.GetScreenRect().GetTopLeft()

    def row(label, widget):
        rect = widget.GetScreenRect()
        print(f"  {label:<18} x={rect.x - origin.x:>5} y={rect.y - origin.y:>4} "
              f"w={rect.width:>4} h={rect.height:>3} "
              f"{'enabled' if widget.IsEnabled() else 'disabled'} "
              f"{'shown' if widget.IsShown() else 'hidden'}")

    print("시험 이름 줄 (좌표는 상세 pane 기준)")
    row("시험 이름", details.heading)
    row("[시험 가져오기]", details.file_buttons["import"])
    row("[시험 내보내기]", details.file_buttons["export"])
    row("상태 칩", details.mode)
    print("시험시나리오 구역")
    row("상태 칩", details.scenario.state)
    row("[CSV 가져오기]", details.scenario.buttons["import"])
    row("[시나리오 비우기]", details.scenario.buttons["clear"])
    frame.Close()
    app.ExitMainLoop()


wx.CallLater(700, dump)
app.MainLoop()
main.dispose()
