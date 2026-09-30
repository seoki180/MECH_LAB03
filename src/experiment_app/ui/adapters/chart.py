"""결과 그래프 하나를 그리는 위젯.

시간(가로)과 값(세로)의 선 그래프다. 한 축 위에 여러 Series를 겹칠 수 있어, 왼쪽
그래프는 측정값과 목표값 두 줄을 같은 축에 그린다.

wx 위젯이지만 계산은 하지 않는다. 받은 Series를 좌표로 옮겨 선으로 잇기만 한다.
값이 없으면 축과 눈금을 지어내지 않고 '표시할 자료 없음'만 적는다 — 빈 격자는
0이 측정된 것처럼 보인다.
"""
import math
from typing import Callable

import wx

from experiment_app.ui.theme import font, MUTED, INK, RULE, SHEET

# 줄 색. 측정과 목표를 색으로 구분한다. 편차는 세 번째 색을 쓴다.
MEASURED = "#1F6FD1"   # SIGNAL
TARGET = "#C8322B"     # STOP
DEVIATION = "#7A3DB8"
ZERO_RULE = "#9AA1A7"

PALETTE = (MEASURED, TARGET, DEVIATION)


class ChartViewport:
    """전체 데이터 범위 안에서 두 축을 포인터 중심으로 확대한다."""

    MAX_SCALE = 64

    def __init__(self, full):
        self.full = full
        self.bounds = full

    def reset(self):
        self.bounds = self.full

    def zoom(self, factor, x_fraction, y_fraction, base=None):
        if self.full is None or not math.isfinite(factor) or factor <= 0:
            return
        base = base if base is not None else self.bounds
        x_fraction = max(0.0, min(1.0, x_fraction))
        y_fraction = max(0.0, min(1.0, y_fraction))
        axes = ((0, x_fraction), (2, 1 - y_fraction))
        updated = []
        for start, fraction in axes:
            low, high = self.full[start:start + 2]
            old_low, old_high = base[start:start + 2]
            full_span = high - low
            span = max(full_span / self.MAX_SCALE,
                       min(full_span, (old_high - old_low) / factor))
            anchor = old_low + fraction * (old_high - old_low)
            new_low = max(low, min(high - span, anchor - fraction * span))
            updated.extend((new_low, new_low + span))
        self.bounds = tuple(updated)

    def pan(self, x_fraction, y_fraction):
        """Drag the visible range by a fraction of its size, staying inside the data."""
        if self.full is None or self.bounds is None:
            return
        updated = []
        for start, fraction in ((0, x_fraction), (2, y_fraction)):
            low, high = self.full[start:start + 2]
            visible_low, visible_high = self.bounds[start:start + 2]
            span = visible_high - visible_low
            moved = max(low, min(high - span, visible_low - fraction * span))
            updated.extend((moved, moved + span))
        self.bounds = tuple(updated)


def nice_step(span, target_count=5):
    """축 눈금 간격을 1·2·5·10… 중에서 고른다."""
    if span <= 0:
        return 1.0
    raw = span / max(1, target_count)
    magnitude = 10 ** math.floor(math.log10(raw))
    for multiple in (1, 2, 5, 10):
        if raw <= multiple * magnitude:
            return multiple * magnitude
    return 10 * magnitude


class ChartView(wx.Panel):
    """render(series, zero_line=False)로 그릴 줄을 받는다."""

    def __init__(self, parent):
        super().__init__(parent)
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.SetBackgroundColour(SHEET)
        self.series = ()
        self.zero_line = False
        self.empty_text = "표시할 자료 없음"
        self.viewport = ChartViewport(None)
        self._gesture_start_bounds = None
        self.drag = None
        self.on_zoom_changed: Callable[[bool], None] | None = None
        self.SetMinSize(self.FromDIP((240, 180)))
        self.Bind(wx.EVT_PAINT, self.paint)
        self.Bind(wx.EVT_SIZE, lambda event: (self.Refresh(False), event.Skip()))
        self.Bind(wx.EVT_MOUSEWHEEL, self.wheel)
        self.Bind(wx.EVT_LEFT_DOWN, self.down)
        self.Bind(wx.EVT_LEFT_UP, self.up)
        self.Bind(wx.EVT_MOTION, self.motion)
        self.Bind(wx.EVT_MOUSE_CAPTURE_LOST, self.capture_lost)
        if self.EnableTouchEvents(wx.TOUCH_ZOOM_GESTURE):
            self.Bind(wx.EVT_GESTURE_ZOOM, self.pinch)

    def render(self, series, zero_line=False, empty_text="표시할 자료 없음"):
        self.series = tuple(s for s in series if s)
        self.zero_line = zero_line
        self.empty_text = empty_text
        self.viewport = ChartViewport(self._data_bounds())
        self._gesture_start_bounds = None
        if self.HasCapture():
            self.ReleaseMouse()
        self.drag = None
        self._notify_zoom()
        self.Refresh(False)

    def _notify_zoom(self):
        if self.on_zoom_changed:
            self.on_zoom_changed(self.bounds() is not None and self.bounds() != self.viewport.full)

    # ------------------------------------------------------------ 축 범위

    def bounds(self):
        """현재 보이는 (t0, t1, v0, v1). 값이 없으면 None."""
        return self.viewport.bounds

    def _data_bounds(self):
        """전체 데이터를 담는 축 범위."""
        if not self.series:
            return None
        times = [t for s in self.series for t, _ in s.points]
        values = [v for s in self.series for _, v in s.points]
        t0, t1 = min(times), max(times)
        v0, v1 = min(values), max(values)
        if self.zero_line:
            # 편차 그래프는 0이 보여야 부호를 읽을 수 있다.
            v0, v1 = min(v0, 0.0), max(v1, 0.0)
        if t1 - t0 <= 0:
            t1 = t0 + 1.0
        if v1 - v0 <= 0:
            # 값이 일정하면 위아래로 약간 벌려 선이 가장자리에 붙지 않게 한다.
            pad = abs(v0) * 0.1 or 1.0
            v0, v1 = v0 - pad, v1 + pad
        return t0, t1, v0, v1

    def reset_zoom(self):
        if self.HasCapture():
            self.ReleaseMouse()
        self.drag = None
        self.viewport.reset()
        self._gesture_start_bounds = None
        self._notify_zoom()
        self.Refresh(False)

    def zoom_at(self, factor, position, base=None):
        width, height = self.GetClientSize()
        left, right = self.FromDIP(52), self.FromDIP(10)
        top, bottom = self.FromDIP(10), self.FromDIP(34)
        if width <= left + right or height <= top + bottom:
            return
        x = (position.x - left) / (width - left - right)
        y = (position.y - top) / (height - top - bottom)
        self.viewport.zoom(factor, x, y, base)
        self._notify_zoom()
        self.Refresh(False)

    def wheel(self, event):
        if event.GetWheelAxis() != wx.MOUSE_WHEEL_VERTICAL:
            event.Skip()
            return
        delta = event.GetWheelDelta() or 120
        # 부모 ScrolledPanel에 휠을 넘기면 그래프 대신 본문이 움직인다.
        self.zoom_at(2 ** (event.GetWheelRotation() / delta * 0.5), event.GetPosition())

    def pinch(self, event):
        if event.IsGestureStart() or self._gesture_start_bounds is None:
            self._gesture_start_bounds = self.bounds()
        # wx의 배율은 제스처 시작 시점 기준 누적값이다.
        self.zoom_at(event.GetZoomFactor(), event.GetPosition(), self._gesture_start_bounds)
        if event.IsGestureEnd():
            self._gesture_start_bounds = None

    def down(self, event):
        if self.bounds() is None or self.bounds() == self.viewport.full:
            return
        self.drag = event.GetPosition()
        self.CaptureMouse()

    def up(self, event):
        if self.HasCapture():
            self.ReleaseMouse()
        self.drag = None

    def motion(self, event):
        if self.drag is None or not event.Dragging() or not event.LeftIsDown():
            return
        position = event.GetPosition()
        width, height = self.GetClientSize()
        plot_w = width - self.FromDIP(62)
        plot_h = height - self.FromDIP(44)
        if plot_w > 0 and plot_h > 0:
            self.viewport.pan((position.x - self.drag.x) / plot_w,
                              (position.y - self.drag.y) / plot_h)
            self._notify_zoom()
            self.Refresh(False)
        self.drag = position

    def capture_lost(self, event):
        self.drag = None

    # ------------------------------------------------------------ 그리기

    def paint(self, event):
        dc = wx.AutoBufferedPaintDC(self)
        dc.SetBackground(wx.Brush(SHEET))
        dc.Clear()
        width, height = self.GetClientSize()
        dc.SetFont(font(11))
        bounds = self.bounds()
        if bounds is None or width < self.FromDIP(80) or height < self.FromDIP(60):
            dc.SetTextForeground(MUTED)
            label = self.empty_text
            size = dc.GetTextExtent(label)
            dc.DrawText(label, max(0, (width - size.width) // 2), max(0, (height - size.height) // 2))
            return
        t0, t1, v0, v1 = bounds
        left, right = self.FromDIP(52), self.FromDIP(10)
        top, bottom = self.FromDIP(10), self.FromDIP(34)
        plot_w, plot_h = width - left - right, height - top - bottom
        if plot_w <= 0 or plot_h <= 0:
            return

        def x_of(t):
            return left + (t - t0) / (t1 - t0) * plot_w

        def y_of(v):
            return top + plot_h - (v - v0) / (v1 - v0) * plot_h

        self._grid(dc, t0, t1, v0, v1, x_of, y_of, left, top, plot_w, plot_h)
        gc = wx.GraphicsContext.Create(dc)
        if gc is None:
            return
        gc.Clip(left, top, plot_w, plot_h)
        for index, series in enumerate(self.series):
            colour = PALETTE[index % len(PALETTE)]
            path = gc.CreatePath()
            for position, (t, v) in enumerate(series.points):
                point = (x_of(t), y_of(v))
                if position == 0:
                    path.MoveToPoint(*point)
                else:
                    path.AddLineToPoint(*point)
            gc.SetPen(wx.Pen(colour, self.FromDIP(2)))
            gc.StrokePath(path)

    def _grid(self, dc, t0, t1, v0, v1, x_of, y_of, left, top, plot_w, plot_h):
        dc.SetTextForeground(MUTED)
        dc.SetPen(wx.Pen(RULE, 1))
        step = nice_step(v1 - v0)
        value = math.ceil(v0 / step) * step
        while value <= v1 + step * 0.001:
            y = int(y_of(value))
            dc.DrawLine(left, y, left + plot_w, y)
            label = f"{value:g}"
            size = dc.GetTextExtent(label)
            dc.DrawText(label, left - size.width - self.FromDIP(6), y - size.height // 2)
            value += step
        t_step = nice_step(t1 - t0)
        when = math.ceil(t0 / t_step) * t_step
        while when <= t1 + t_step * 0.001:
            x = int(x_of(when))
            dc.DrawLine(x, top, x, top + plot_h)
            label = f"{when:g}"
            size = dc.GetTextExtent(label)
            dc.DrawText(label, x - size.width // 2, top + plot_h + self.FromDIP(6))
            when += t_step
        # 축 테두리
        dc.SetPen(wx.Pen(INK, 1))
        dc.DrawLine(left, top, left, top + plot_h)
        dc.DrawLine(left, top + plot_h, left + plot_w, top + plot_h)
        if self.zero_line and v0 <= 0 <= v1:
            dc.SetPen(wx.Pen(ZERO_RULE, self.FromDIP(1), wx.PENSTYLE_SHORT_DASH))
            y = int(y_of(0.0))
            dc.DrawLine(left, y, left + plot_w, y)

    def dispose(self):
        if self.HasCapture():
            self.ReleaseMouse()
        self.drag = None
        self.series = ()
        self.viewport = ChartViewport(None)
        self._gesture_start_bounds = None
        self.on_zoom_changed = None
