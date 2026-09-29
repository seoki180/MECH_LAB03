"""로컬 타일팩을 배경으로 GPS 궤적을 그리는 지도 view.

네트워크를 사용하지 않는다. 타일은 주입된 MapTileSource에서만 가져오므로
이 모듈은 지도 공급자와 타일 URL을 알지 못한다 (UI_SPEC.md 4.3).
"""
import io
import math
import wx
from experiment_app.ui.adapters.mercator import TILE_SIZE, lonlat_to_pixel, pixel_to_lonlat, zoom_about

# 실제로 타일이 있는 구간. 이 밖으로 나가면 격자만 뜨거나 흐릿하게 늘어난 그림이 된다.
# 팩들의 줌 범위를 합친 값은 구역마다 타일 유무가 달라 경계로 쓸 수 없다.
MIN_ZOOM, MAX_ZOOM = 13, 19
BITMAP_CACHE_LIMIT = 256
# 휠 한 노치가 움직이는 줌 단계. 트랙패드는 잘게 여러 번 보내므로 1보다 작게 둔다.
WHEEL_STEP = 0.5


class TileMapView(wx.Panel):
    def __init__(self, parent, on_pan, tiles):
        super().__init__(parent)
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.SetMinSize(self.FromDIP((200, 130)))
        self.tiles = tiles
        self.track = ()
        # GPS fix 전에는 타일팩 중심을 보여준다. 팩이 없으면 첫 fix까지 배경만 그린다.
        self.center = tiles.center
        self.zoom = 15
        self.drag = None
        self.failed = False
        self.valid = False
        self.follow = False
        self.on_pan = on_pan
        self._bitmaps = {}
        self._gesture_zoom = None
        self.Bind(wx.EVT_PAINT, self.paint)
        self.Bind(wx.EVT_LEFT_DOWN, self.down)
        self.Bind(wx.EVT_LEFT_UP, self.up)
        self.Bind(wx.EVT_MOTION, self.motion)
        self.Bind(wx.EVT_MOUSEWHEEL, self.wheel)
        self.Bind(wx.EVT_MOUSE_CAPTURE_LOST, lambda e: setattr(self, "drag", None))
        # 터치를 지원하지 않는 플랫폼에서는 죽은 핸들러를 남기지 않는다.
        if self.EnableTouchEvents(wx.TOUCH_ZOOM_GESTURE):
            self.Bind(wx.EVT_GESTURE_ZOOM, self.pinch)

    def render(self, track, follow, failed=False, valid=True):
        self.track, self.failed, self.valid, self.follow = track, failed, valid, follow
        if track and (follow or self.center is None):
            self.center = track[-1]
        self.Refresh(False)

    def zoom_to(self, zoom, anchor=None):
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        if zoom == self.zoom:
            return
        # 추적 중에는 render()가 중심을 현재 위치로 되돌리므로 앵커 보정이 튕겨 돌아온다.
        if anchor is not None and self.center and not (self.follow and self.track):
            width, height = self.GetClientSize()
            offset = (anchor[0] - width / 2, anchor[1] - height / 2)
            self.center = zoom_about(self.center, offset, self.zoom, zoom)
        self.zoom = zoom
        self.Refresh(False)

    def zoom_by(self, steps):
        # 버튼은 핀치가 남긴 소수 줌에서도 정수 단계로 떨어지게 한다.
        self.zoom_to(round(self.zoom) + steps)

    def wheel(self, event):
        if event.GetWheelAxis() != wx.MOUSE_WHEEL_VERTICAL:
            return
        delta = event.GetWheelDelta() or 120
        # Skip()을 부르지 않는다. ScrolledPanel 본문으로 넘어가면 확대 대신 화면이 스크롤된다.
        self.zoom_to(self.zoom + event.GetWheelRotation() / delta * WHEEL_STEP, event.GetPosition())

    def pinch(self, event):
        if event.IsGestureStart() or self._gesture_zoom is None:
            self._gesture_zoom = self.zoom
        factor = event.GetZoomFactor()
        if factor > 0:
            # GetZoomFactor()는 제스처 시작 대비 누적 배율이라 증분으로 더하면 드리프트가 생긴다.
            self.zoom_to(self._gesture_zoom + math.log2(factor), event.GetPosition())
        if event.IsGestureEnd():
            self._gesture_zoom = None

    def down(self, event):
        self.drag = event.GetPosition()
        self.CaptureMouse()
        self.on_pan()

    def up(self, event):
        if self.HasCapture():
            self.ReleaseMouse()
        self.drag = None

    def motion(self, event):
        if not self.follow and self.drag is not None and event.Dragging() and self.center:
            position = event.GetPosition()
            x, y = lonlat_to_pixel(self.center[1], self.center[0], self.zoom)
            lon, lat = pixel_to_lonlat(x - (position.x - self.drag.x), y - (position.y - self.drag.y), self.zoom)
            self.center = (lat, lon)
            self.drag = position
            self.Refresh(False)

    def bitmap(self, zoom, x, y):
        key = (zoom, x, y)
        if key not in self._bitmaps:
            if len(self._bitmaps) >= BITMAP_CACHE_LIMIT:
                self._bitmaps.clear()
            data = self.tiles.tile(zoom, x, y)
            # 디코딩 실패는 IsOk()로 받는다. wx 기본 로그는 타일마다 모달을 띄우므로 막는다.
            with wx.LogNull():
                image = wx.Image(io.BytesIO(data), wx.BITMAP_TYPE_ANY) if data else None
            self._bitmaps[key] = image.ConvertToBitmap() if image and image.IsOk() else None
        return self._bitmaps[key]

    def draw_tiles(self, gc, width, height):
        """보이는 타일을 그리고, 배경을 한 장이라도 채웠는지 돌려준다."""
        # 타일은 정수 줌에서만 존재한다. 소수부는 타일을 늘려 그려서 메운다.
        source_zoom = min(int(self.zoom), self.tiles.max_zoom)
        span = TILE_SIZE * 2.0 ** (self.zoom - source_zoom)
        center_x, center_y = lonlat_to_pixel(self.center[1], self.center[0], self.zoom)
        left, top = center_x - width / 2, center_y - height / 2
        drawn = False
        for tile_x in range(int(left // span), int((left + width) // span) + 1):
            for tile_y in range(int(top // span), int((top + height) // span) + 1):
                limit = 1 << source_zoom
                if not (0 <= tile_x < limit and 0 <= tile_y < limit):
                    continue
                bitmap = self.bitmap(source_zoom, tile_x, tile_y)
                if bitmap:
                    gc.DrawBitmap(bitmap, tile_x * span - left, tile_y * span - top, span, span)
                    drawn = True
        return drawn

    def draw_grid(self, gc, width, height):
        gc.SetPen(wx.Pen("#D8E1E8"))
        gap = self.FromDIP(32)
        for x in range(0, width, gap):
            gc.StrokeLine(x, 0, x, height)
        for y in range(0, height, gap):
            gc.StrokeLine(0, y, width, y)

    def draw_track(self, gc, width, height):
        center_x, center_y = lonlat_to_pixel(self.center[1], self.center[0], self.zoom)
        points = []
        for lat, lon in self.track:
            x, y = lonlat_to_pixel(lon, lat, self.zoom)
            points.append((x - center_x + width / 2, y - center_y + height / 2))
        if len(points) > 1:
            path = gc.CreatePath()
            path.MoveToPoint(*points[0])
            for point in points[1:]:
                path.AddLineToPoint(*point)
            gc.SetPen(wx.Pen("#1557B0", 3))
            gc.StrokePath(path)
        gc.SetPen(wx.Pen("#FFFFFF", 2))
        gc.SetBrush(wx.Brush("#1557B0" if self.valid else "#88939E"))
        radius = self.FromDIP(6)
        gc.DrawEllipse(points[-1][0] - radius, points[-1][1] - radius, radius * 2, radius * 2)

    def paint(self, event):
        dc = wx.AutoBufferedPaintDC(self)
        dc.SetBackground(wx.Brush("#EDF2F5"))
        dc.Clear()
        width, height = self.GetClientSize()
        gc = wx.GraphicsContext.Create(dc)
        if not gc:
            return
        drawn = self.center is not None and not self.failed and self.draw_tiles(gc, width, height)
        if not drawn:
            self.draw_grid(gc, width, height)
        if self.center and self.track:
            self.draw_track(gc, width, height)
        gc.SetFont(self.GetFont(), wx.Colour("#17212B" if drawn else "#596A7A"))
        gc.DrawText(self.notice(drawn), 12, 12)
        if drawn and self.tiles.attribution:
            gc.DrawText(self.tiles.attribution, 12, height - self.FromDIP(22))

    def notice(self, drawn):
        if self.failed:
            return "지도 배경 실패 · 좌표/궤적 유지"
        if not self.tiles.available:
            return "오프라인 타일팩 없음 · 좌표/궤적 유지"
        if not drawn:
            return "이 구역의 타일 없음 · 좌표/궤적 유지"
        return f"z{self.zoom:.1f}" + (" · 확대 표시" if self.zoom > self.tiles.max_zoom else "")

    def dispose(self):
        if self.HasCapture():
            self.ReleaseMouse()
        self._bitmaps.clear()
