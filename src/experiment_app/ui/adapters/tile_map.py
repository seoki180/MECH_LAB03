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
# 아래 한계는 전국 개요 팩(z0–11)을 함께 두는 구성을 전제로 한다. 구역 팩 밖으로
# 나갔을 때 축소해서 '지금 어디쯤인지'를 볼 수 있어야 하므로 13이 아니라 8이다.
MIN_ZOOM, MAX_ZOOM = 8, 19
BITMAP_CACHE_LIMIT = 256
# 휠 한 노치가 움직이는 줌 단계. 트랙패드는 잘게 여러 번 보내므로 1보다 작게 둔다.
WHEEL_STEP = 0.5
# 타일팩도 GPS fix도 없을 때 지도를 둘 자리(한반도 중앙). **표시 시점일 뿐 측정값이
# 아니다.** 좌표 readout과 궤적은 이 값을 쓰지 않는다. 중심이 비어 있으면 끌기조차
# 기준이 없어 지도가 멈춘 것처럼 보이므로 시점만 채워 둔다.
FALLBACK_CENTER = (36.5, 127.8)


class TileMapView(wx.Panel):
    def __init__(self, parent, on_pan, tiles):
        super().__init__(parent)
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.SetMinSize(self.FromDIP((200, 130)))
        self.tiles = tiles
        self.track = ()
        # GPS fix 전에는 타일팩 중심을 보여준다. 팩도 없으면 임시 시점을 쓰고
        # 첫 fix에서 실제 위치로 옮긴다(guess 플래그).
        self.center = tiles.center or FALLBACK_CENTER
        self.center_is_guess = tiles.center is None
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
        if track and (follow or self.center_is_guess):
            self.center = track[-1]
            self.center_is_guess = False
        self.Refresh(False)

    def recenter(self):
        """추적을 켜고 중심을 현재 위치로 되돌린다.

        유효 fix가 하나도 없으면 돌아갈 '현재 위치'가 없다. 그때는 타일팩 중심
        (개요 팩이 있으면 그 중앙)으로 보내 적어도 배경이 보이는 자리에 둔다.
        """
        self.follow = True
        self.center = self.track[-1] if self.track else (self.tiles.center or FALLBACK_CENTER)
        self.center_is_guess = not self.track
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
        """타일이 없는 구역의 배경 격자.

        격자를 화면에 고정하면 끌어도 그림이 그대로라 지도가 멈춘 것처럼 보인다.
        지도 좌표(웹 메르카토르 픽셀)에 맞춰 흘려서 끌기·확대가 눈에 보이게 한다.
        """
        gc.SetPen(wx.Pen("#D8E1E8"))
        gap = self.FromDIP(32)
        center_x, center_y = lonlat_to_pixel(self.center[1], self.center[0], self.zoom)
        left, top = center_x - width / 2, center_y - height / 2
        offset_x, offset_y = -(left % gap), -(top % gap)
        x = offset_x
        while x < width:
            gc.StrokeLine(x, 0, x, height)
            x += gap
        y = offset_y
        while y < height:
            gc.StrokeLine(0, y, width, y)
            y += gap

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
        drawn = not self.failed and self.draw_tiles(gc, width, height)
        if not drawn:
            self.draw_grid(gc, width, height)
        if self.track:
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
            return "이 구역의 타일 없음 · 축소하면 개요 팩 · 좌표/궤적 유지"
        return f"z{self.zoom:.1f}" + (" · 확대 표시" if self.zoom > self.tiles.max_zoom else "")

    def dispose(self):
        if self.HasCapture():
            self.ReleaseMouse()
        self._bitmaps.clear()
