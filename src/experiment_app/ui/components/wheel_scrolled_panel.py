"""Scrolled panel that handles wheel input over its child controls as well."""
import wx
from wx.lib.scrolledpanel import ScrolledPanel


class WheelScrolledPanel(ScrolledPanel):
    def __init__(self, parent):
        super().__init__(parent)
        self._wheel_remainder = 0

    def bind_wheel_children(self, exclude=()):
        """Call after building or replacing controls inside the scrolling area."""
        def bind(window):
            for child in window.GetChildren():
                if child in exclude:
                    continue
                if getattr(child, "_wheel_scroll_owner", None) is not self:
                    child.Bind(wx.EVT_MOUSEWHEEL, self._child_wheel)
                    child._wheel_scroll_owner = self
                bind(child)
        bind(self)

    def _child_wheel(self, event):
        if event.GetWheelAxis() != wx.MOUSE_WHEEL_VERTICAL:
            event.Skip()
            return
        delta = event.GetWheelDelta()
        if not delta or not self.GetScrollPixelsPerUnit()[1]:
            event.Skip()
            return
        self._wheel_remainder += event.GetWheelRotation()
        steps = abs(self._wheel_remainder) // delta
        if not steps:
            return
        direction = 1 if self._wheel_remainder < 0 else -1
        self._wheel_remainder -= (1 if self._wheel_remainder > 0 else -1) * steps * delta
        self.Scroll(-1, self.GetViewStart()[1] + direction * steps * max(1, event.GetLinesPerAction()))
