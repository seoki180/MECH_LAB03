"""Opt-in scroll event regression check: .venv/bin/python tests/scroll_smoke.py."""
import wx
from experiment_app.ui.components.wheel_scrolled_panel import WheelScrolledPanel

app = wx.App(False)
frame = wx.Frame(None, size=wx.Size(480, 360))
panel = WheelScrolledPanel(frame)
content = wx.BoxSizer(wx.VERTICAL)
label = wx.StaticText(panel, label="Scroll event probe")
content.Add(label)
content.AddSpacer(2400)
panel.SetSizer(content)
panel.SetupScrolling(scroll_x=False, rate_y=16)
root = wx.BoxSizer(wx.VERTICAL)
root.Add(panel, 1, wx.EXPAND)
frame.SetSizer(root)
frame.Show()
wx.Yield()
panel.bind_wheel_children()
panel.bind_wheel_children()


def wheel(rotation):
    event = wx.MouseEvent(wx.wxEVT_MOUSEWHEEL)
    event.SetWheelAxis(wx.MOUSE_WHEEL_VERTICAL)
    event.SetWheelDelta(120)
    event.SetLinesPerAction(3)
    event.SetWheelRotation(rotation)
    label.GetEventHandler().ProcessEvent(event)


try:
    panel.Scroll(0, 0)
    wheel(-30)
    assert panel.GetViewStart()[1] > 0, "Small wheel input waits for a full notch then jumps"
    first = panel.GetViewStart()[1]
    for _ in range(3):
        wheel(-30)
    split = panel.GetViewStart()[1]
    panel.Scroll(0, 0)
    wheel(-120)
    assert panel.GetViewStart()[1] == split == first * 4, "Wheel deltas must be proportional, without duplicate bindings"
    panel.Scroll(0, 0)
    wheel(120)
    assert panel.GetViewStart()[1] == 0
    wheel(-120)
    assert panel.GetViewStart()[1] == split
    print("PASS: fractional wheel input, single dispatch, direction and top boundary")
    root.Detach(panel)
    root.Add(panel.with_scrollbar(), 1, wx.EXPAND)
    frame.Layout()
    wx.Yield()
    bar = panel.scrollbar
    assert bar is not None
    assert bar.ToDIP(bar.GetSize()).width == 48, "Scrollbar must have a 48 DIP hit target"
    bar.SetFocus()
    event = wx.KeyEvent(wx.wxEVT_KEY_DOWN)
    event.SetKeyCode(wx.WXK_END)
    bar.GetEventHandler().ProcessEvent(event)
    wx.Yield()
    maximum = panel.GetVirtualSize().height - panel.GetClientSize().height
    assert abs(panel.GetViewStart()[1] - maximum) <= 1
    event.SetKeyCode(wx.WXK_HOME)
    bar.GetEventHandler().ProcessEvent(event)
    assert panel.GetViewStart()[1] == 0
    assert bar.thumb_rect().height >= bar.FromDIP(48)
    bar.Refresh()
    bar.Update()
    print("PASS: scrollbar width, thumb minimum, Home/End and native paint")
    down = wx.MouseEvent(wx.wxEVT_LEFT_DOWN)
    down.SetPosition(wx.Point(24, 10))
    bar.GetEventHandler().ProcessEvent(down)
    assert bar.HasCapture()
    motion = wx.MouseEvent(wx.wxEVT_MOTION)
    motion.SetLeftDown(True)
    motion.SetPosition(wx.Point(24, bar.GetClientSize().height + 100))
    bar.GetEventHandler().ProcessEvent(motion)
    assert abs(panel.GetViewStart()[1] - maximum) <= 1
    up = wx.MouseEvent(wx.wxEVT_LEFT_UP)
    bar.GetEventHandler().ProcessEvent(up)
    assert not bar.HasCapture()
    event.SetKeyCode(wx.WXK_HOME)
    bar.GetEventHandler().ProcessEvent(event)
    down.SetPosition(wx.Point(24, bar.GetClientSize().height - 1))
    bar.GetEventHandler().ProcessEvent(down)
    assert panel.GetViewStart()[1] == panel.GetClientSize().height
    assert not bar.HasCapture()
    for _ in range(3):
        panel.SetClientSize(wx.Size(panel.GetClientSize().width, 240))
        panel.FitInside()
        wx.Yield()
        assert bar.extent == panel.GetVirtualSize().height
        assert bar.viewport == panel.GetClientSize().height
    content.Clear(False)
    content.Add(label)
    panel.FitInside()
    wx.Yield()
    assert panel.GetViewStart()[1] == 0
    assert bar.extent <= bar.viewport
    print("PASS: thumb drag/clamp, release, page click, resize and shrinking content")
finally:
    frame.Destroy()
    wx.Yield()
