"""Scrolled panel that handles wheel, touch pan and finger drag over its children.

마우스 휠만 처리하면 태블릿에서 스크롤이 되지 않는다. 손가락 입력은 두 가지
경로로 들어온다.

1. 운영체제가 pan 제스처로 바꿔주는 경우: EVT_GESTURE_PAN. 제스처는 명령
   이벤트가 아니어서 부모로 올라가지 않으므로 스크롤 영역 안의 자식마다
   직접 연결해야 한다.
2. 제스처 없이 마우스로 흉내내는 경우: 버튼 위에서 손가락을 끌면 버튼이
   마우스를 잡아 스크롤이 전혀 일어나지 않는다. 일정 거리 이상 끌리면 그
   눌림을 취소하고 본문을 끌어 움직인다.
"""
import wx
from wx.lib.scrolledpanel import ScrolledPanel
from experiment_app.ui.components.touch_scrollbar import TouchScrollbar

# 이 거리(DIP)를 넘겨 끌면 눌림이 아니라 스크롤로 본다. 태블릿에서 손가락은
# 버튼을 누를 때도 몇 픽셀 흔들린다.
DRAG_THRESHOLD = 10


class WheelScrolledPanel(ScrolledPanel):
    def __init__(self, parent):
        super().__init__(parent)
        self._wheel_line_pixels = self.FromDIP(16)
        self._scroll_remainder = 0
        self._drag_from = None
        self._dragging = False
        self._bind_scroll_input(self)
        self.scrollbar = None
        self._scroll_sizer = None

    def SetupScrolling(self, scroll_x=True, scroll_y=True, rate_x=20, rate_y=20,
                       scrollToTop=True, scrollIntoView=True):
        # Keep wheel line distance, but let pan/high-resolution wheel move by pixels.
        self._wheel_line_pixels = self.FromDIP(rate_y)
        super().SetupScrolling(scroll_x, scroll_y, rate_x, 1 if rate_y else 0,
                               scrollToTop, scrollIntoView)

    def with_scrollbar(self):
        """Add this sizer, rather than the panel, to its parent's layout."""
        if self._scroll_sizer is None:
            self.ShowScrollbars(wx.SHOW_SB_NEVER, wx.SHOW_SB_NEVER)
            self.scrollbar = TouchScrollbar(self.GetParent(), self._scroll_to_pixel,
                                           self._child_wheel)
            self._scroll_sizer = wx.BoxSizer(wx.HORIZONTAL)
            self._scroll_sizer.Add(self, 1, wx.EXPAND)
            self._scroll_sizer.Add(self.scrollbar, 0, wx.EXPAND)
            self.Bind(wx.EVT_IDLE, self._sync_scrollbar)
        return self._scroll_sizer

    def _scroll_to_pixel(self, position):
        unit = self.GetScrollPixelsPerUnit()[1]
        if unit:
            maximum = max(0, self.GetVirtualSize().height - self.GetClientSize().height)
            self.Scroll(-1, round(max(0, min(position, maximum)) / unit))
            self._sync_scrollbar()

    def _sync_scrollbar(self, event=None):
        if self.scrollbar:
            self.scrollbar.render(self.GetViewStart()[1] * self.GetScrollPixelsPerUnit()[1],
                                  self.GetClientSize().height, self.GetVirtualSize().height)
        if event:
            event.Skip()

    def bind_wheel_children(self, exclude=()):
        """Call after building or replacing controls inside the scrolling area."""
        def bind(window):
            for child in window.GetChildren():
                if child in exclude:
                    continue
                if getattr(child, "_wheel_scroll_owner", None) is not self:
                    self._bind_scroll_input(child)
                    child._wheel_scroll_owner = self
                bind(child)
        bind(self)

    def _bind_scroll_input(self, window):
        """끌기 좌표를 어느 창에서 받았는지 알아야 하므로 창을 함께 묶어둔다."""
        window.Bind(wx.EVT_MOUSEWHEEL, self._child_wheel)
        window.Bind(wx.EVT_LEFT_DOWN, lambda event, w=window: self._press(event, w))
        window.Bind(wx.EVT_MOTION, lambda event, w=window: self._motion(event, w))
        window.Bind(wx.EVT_LEFT_UP, self._release)
        window.Bind(wx.EVT_MOUSE_CAPTURE_LOST, self._release)
        # 제스처를 못 받는 컨트롤도 있다. 그 자리는 끌기 경로가 맡는다.
        if window.EnableTouchEvents(wx.TOUCH_VERTICAL_PAN_GESTURE):
            window.Bind(wx.EVT_GESTURE_PAN, self._pan)

    # --------------------------------------------------------------- 스크롤

    def scroll_by_pixels(self, dy):
        """양수면 본문이 위로 올라가며 뒤쪽 내용이 보인다. 남는 픽셀은 모아둔다."""
        unit = self.GetScrollPixelsPerUnit()[1]
        if not unit:
            return False
        self._scroll_remainder += dy
        units = int(self._scroll_remainder / unit)
        if not units:
            return False
        self._scroll_remainder -= units * unit
        before = self.GetViewStart()[1]
        self.Scroll(-1, max(0, before + units))
        if self.GetViewStart()[1] == before:
            self._scroll_remainder = 0
            return False
        return True

    # --------------------------------------------------------------- 입력

    def _child_wheel(self, event):
        if event.GetWheelAxis() != wx.MOUSE_WHEEL_VERTICAL:
            event.Skip()
            return
        delta = event.GetWheelDelta()
        if not delta or not self.GetScrollPixelsPerUnit()[1]:
            event.Skip()
            return
        distance = (self.GetClientSize().height if event.IsPageScroll()
                    else self._wheel_line_pixels * max(1, event.GetLinesPerAction()))
        self.scroll_by_pixels(-event.GetWheelRotation() / delta * distance)

    def _pan(self, event):
        if event.IsGestureStart():
            self._scroll_remainder = 0
        # 손가락을 올리면 뒤쪽 내용이 보인다 — 화면이 손가락을 따라간다.
        self.scroll_by_pixels(-event.GetDelta().y)

    def _press(self, event, window):
        self._drag_from = window.ClientToScreen(event.GetPosition())
        self._dragging = False
        self._scroll_remainder = 0
        event.Skip()

    def _motion(self, event, window):
        event.Skip()
        if self._drag_from is None or not event.Dragging() or not event.LeftIsDown():
            return
        position = window.ClientToScreen(event.GetPosition())
        dy = position.y - self._drag_from.y
        if not self._dragging:
            if abs(dy) < self.FromDIP(DRAG_THRESHOLD):
                return
            # 여기부터는 누름이 아니라 스크롤이다. 버튼이 눌린 채로 남거나
            # 손을 뗄 때 실행되지 않게 눌림을 취소한다.
            self._dragging = True
            self._cancel_press(window)
        self._drag_from = position
        self.scroll_by_pixels(-dy)

    def _release(self, event):
        self._drag_from = None
        self._dragging = False
        event.Skip()

    @staticmethod
    def _cancel_press(window):
        if window.HasCapture():
            window.ReleaseMouse()
        cancel = getattr(window, "cancel_press", None)
        if cancel:
            cancel()
