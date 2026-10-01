"""Opt-in native smoke for wheel/pinch on the result charts."""
import math
import wx

from experiment_app.domain.analysis import analyse
from experiment_app.domain.scenario import ScenarioPoint
from experiment_app.ui.panes.result_charts import ResultChartsPane


def wheel(view, notches, position):
    event = wx.MouseEvent(wx.wxEVT_MOUSEWHEEL)
    event.SetWheelAxis(wx.MOUSE_WHEEL_VERTICAL)
    event.SetWheelDelta(120)
    event.SetWheelRotation(120 * notches)
    event.SetPosition(wx.Point(*position))
    view.GetEventHandler().ProcessEvent(event)


def pinch(view, factor, position, start=False, end=False):
    event = wx.ZoomGestureEvent()
    event.SetEventObject(view)
    event.SetGestureStart(start)
    event.SetGestureEnd(end)
    event.SetZoomFactor(factor)
    event.SetPosition(wx.Point(*position))
    view.GetEventHandler().ProcessEvent(event)


app = wx.App(False)
frame = wx.Frame(None, size=wx.Size(1024, 768))
pane = ResultChartsPane(frame)
frame.SetSizer(wx.BoxSizer(wx.VERTICAL))
frame.GetSizer().Add(pane, 1, wx.EXPAND)
frame.Show()
try:
    result = analyse([(100.0, 10.0, "정상"), (101.0, 18.0, "정상"),
                      (102.0, 25.0, "정상")],
                     (ScenarioPoint(0.0, 5.0), ScenarioPoint(2.0, 15.0)),
                     "C.0", "현재 속도", "km/h")
    pane.render(result)
    frame.Layout()
    wx.Yield()
    speed, deviation = pane.speed.chart, pane.deviation.chart
    origin = speed.bounds()
    other = deviation.bounds()
    assert origin is not None and other is not None
    assert not pane.speed.reset_button.IsEnabled()
    center = (speed.GetClientSize().width // 2, speed.GetClientSize().height // 2)
    wheel(speed, 1, center)
    assert pane.speed.reset_button.IsEnabled()
    zoomed = speed.bounds()
    assert zoomed is not None and zoomed[1] - zoomed[0] < origin[1] - origin[0]
    assert deviation.bounds() == other
    down = wx.MouseEvent(wx.wxEVT_LEFT_DOWN)
    down.SetPosition(wx.Point(*center))
    speed.GetEventHandler().ProcessEvent(down)
    assert speed.HasCapture()
    moved = wx.MouseEvent(wx.wxEVT_MOTION)
    moved.SetLeftDown(True)
    moved.SetPosition(wx.Point(center[0] + 40, center[1] + 30))
    speed.GetEventHandler().ProcessEvent(moved)
    panned = speed.bounds()
    # 아래로 끌면 그림이 따라 내려가 더 높은 값 구간이 보인다.
    assert panned[0] < zoomed[0] and panned[2] > zoomed[2]
    assert deviation.bounds() == other
    up = wx.MouseEvent(wx.wxEVT_LEFT_UP)
    speed.GetEventHandler().ProcessEvent(up)
    assert not speed.HasCapture() and speed.drag is None
    wheel(speed, -20, center)
    assert speed.bounds() == origin
    pinch(speed, 2.0, center, start=True)
    pinch(speed, 3.0, center, end=True)
    pinched = speed.bounds()
    assert pinched is not None and math.isclose(pinched[1] - pinched[0],
                                                  (origin[1] - origin[0]) / 3)
    pane.speed.reset_button.Notify()
    assert speed.bounds() == origin
    assert not pane.speed.reset_button.IsEnabled()
    reset_height = pane.speed.reset_button.ToDIP(pane.speed.reset_button.GetSize()).height
    assert reset_height >= 48, reset_height
    wheel(deviation, 1, (deviation.GetClientSize().width // 2,
                         deviation.GetClientSize().height // 2))
    assert pane.deviation.reset_button.IsEnabled() and speed.bounds() == origin
    pane.render(result)
    assert deviation.bounds() == other and not pane.deviation.reset_button.IsEnabled()
    speed.Refresh()
    speed.Update()
    pane.render(analyse([], (), "C.0", "현재 속도", "km/h"))
    wheel(speed, 1, center)
    assert speed.bounds() is None
    print("chart wheel/pinch/reset/empty smoke passed")
finally:
    pane.dispose()
    frame.Destroy()
