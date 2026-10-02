"""Fixed-width vertical scrollbar with a touch-sized thumb and keyboard access."""
import wx
from experiment_app.ui.theme import SCROLLBAR_WIDTH, SCROLLBAR_THUMB_MIN, RULE, MUTED, SIGNAL, SHEET


class TouchScrollbar(wx.Control):
    def __init__(self, parent, on_scroll, on_wheel):
        super().__init__(parent, style=wx.WANTS_CHARS | wx.BORDER_NONE)
        self.SetName("세로 스크롤")
        self.SetMinSize(self.FromDIP((SCROLLBAR_WIDTH, SCROLLBAR_THUMB_MIN)))
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.on_scroll = on_scroll
        self.position, self.viewport, self.extent = 0, 0, 0
        self._anchor = None
        self.Bind(wx.EVT_PAINT, self._paint)
        self.Bind(wx.EVT_SIZE, self._size)
        self.Bind(wx.EVT_LEFT_DOWN, self._down)
        self.Bind(wx.EVT_MOTION, self._motion)
        self.Bind(wx.EVT_LEFT_UP, self._up)
        self.Bind(wx.EVT_MOUSE_CAPTURE_LOST, self._lost)
        self.Bind(wx.EVT_KEY_DOWN, self._key)
        self.Bind(wx.EVT_MOUSEWHEEL, on_wheel)
        self.Bind(wx.EVT_SET_FOCUS, self._focus)
        self.Bind(wx.EVT_KILL_FOCUS, self._focus)

    def render(self, position, viewport, extent):
        state = (position, viewport, extent)
        if state != (self.position, self.viewport, self.extent):
            self.position, self.viewport, self.extent = state
            self.Refresh(False)

    def thumb_rect(self):
        width, height = self.GetClientSize()
        length = min(height, max(self.FromDIP(SCROLLBAR_THUMB_MIN),
                                round(height * self.viewport / max(1, self.extent))))
        maximum = max(0, self.extent - self.viewport)
        top = round((height - length) * min(self.position, maximum) / max(1, maximum))
        return wx.Rect(0, top, width, length)

    def _paint(self, event):
        dc = wx.AutoBufferedPaintDC(self)
        dc.SetBackground(wx.Brush(SHEET))
        dc.Clear()
        width, height = self.GetClientSize()
        inset = self.FromDIP(4)
        dc.SetPen(wx.TRANSPARENT_PEN)
        dc.SetBrush(wx.Brush(RULE))
        dc.DrawRoundedRectangle(inset, 0, max(0, width - 2 * inset), height, inset)
        if self.extent > self.viewport:
            rect = self.thumb_rect()
            dc.SetBrush(wx.Brush(SIGNAL if self.HasFocus() or self._anchor is not None else MUTED))
            dc.DrawRoundedRectangle(inset, rect.y, max(0, width - 2 * inset), rect.height, inset)

    def _size(self, event):
        self.Refresh(False)
        event.Skip()

    def _focus(self, event):
        self.Refresh(False)
        event.Skip()

    def _down(self, event):
        self.SetFocus()
        if self.extent <= self.viewport:
            return
        rect = self.thumb_rect()
        if rect.Contains(event.GetPosition()):
            self._anchor = event.GetY() - rect.y
            self.CaptureMouse()
        else:
            direction = -1 if event.GetY() < rect.y else 1
            self.on_scroll(self.position + direction * self.viewport)
        self.Refresh(False)

    def _motion(self, event):
        if self._anchor is None:
            return
        travel = self.GetClientSize().height - self.thumb_rect().height
        fraction = max(0, min(1, (event.GetY() - self._anchor) / max(1, travel)))
        self.on_scroll(round(fraction * max(0, self.extent - self.viewport)))

    def _up(self, event):
        self.dispose()
        self.Refresh(False)

    def _lost(self, event):
        self._anchor = None
        self.Refresh(False)

    def _key(self, event):
        key = event.GetKeyCode()
        targets = {wx.WXK_HOME: 0, wx.WXK_END: self.extent - self.viewport,
                   wx.WXK_UP: self.position - self.FromDIP(16),
                   wx.WXK_DOWN: self.position + self.FromDIP(16),
                   wx.WXK_PAGEUP: self.position - self.viewport,
                   wx.WXK_PAGEDOWN: self.position + self.viewport}
        if key in targets:
            self.on_scroll(targets[key])
        else:
            event.Skip()

    def dispose(self):
        self._anchor = None
        if self.HasCapture():
            self.ReleaseMouse()
