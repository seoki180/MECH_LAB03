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
footer = wx.TextCtrl(panel, value="Last input must remain reachable")
content.Add(footer, 0, wx.EXPAND)
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
    assert list(frame.GetChildren()) == [panel], "Do not add a sibling scrollbar or overlay"
    for size in (wx.Size(480, 360), wx.Size(360, 240), wx.Size(640, 480)):
        frame.SetClientSize(size)
        frame.Layout()
        panel.FitInside()
        wx.Yield()
        maximum = panel.GetVirtualSize().height - panel.GetClientSize().height
        event = wx.ScrollWinEvent(wx.wxEVT_SCROLLWIN_THUMBTRACK, maximum, wx.VERTICAL)
        panel.GetEventHandler().ProcessEvent(event)
        wx.Yield()
        assert abs(panel.GetViewStart()[1] - maximum) <= 1
        rect = wx.Rect(panel.ClientToScreen(wx.Point(0, 0)), panel.GetClientSize())
        assert rect.Contains(footer.GetScreenRect()), "Bottom input is clipped"
    content.Clear(False)
    footer.Hide()
    content.Add(label)
    panel.FitInside()
    wx.Yield()
    assert panel.GetViewStart()[1] == 0
    print("PASS: native thumb events, bottom input visibility, resize and content shrink")
finally:
    frame.Destroy()
    wx.Yield()
