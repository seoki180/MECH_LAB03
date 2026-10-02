import math
import wx
from experiment_app.ui.adapters.tile_map import TileMapView
from experiment_app.ui.theme import text, button, add, stroke, section_bar, SHEET, MUTED

# 화면에 보이는 좌표의 소수점 자릿수. 표시에만 적용하며 기록·복사는 원본 정밀도를
# 유지한다(copy_service는 repr로 그대로 쓴다).
COORDINATE_DIGITS = 2


def format_coordinate(value):
    """좌표를 소수점 아래 COORDINATE_DIGITS 자리까지 남기고 **버린다**.

    반올림하지 않는다. 37.4599를 37.46으로 올리면 실제로 가 보지 않은 자리를
    가리키게 되므로, 남은 자리는 버려서 표시값이 항상 실제 좌표 쪽에 있게 한다.
    """
    if value is None:
        return "—"
    scale = 10 ** COORDINATE_DIGITS
    # 0을 향해 버린다. 음수(남위·서경)도 절댓값이 커지지 않는다.
    truncated = math.trunc(value * scale) / scale
    return f"{truncated:.{COORDINATE_DIGITS}f}"


class GpsMapPane(wx.Panel):
    def __init__(self, parent, tiles):
        super().__init__(parent)
        self.SetBackgroundColour(SHEET)
        self.follow = True
        root = wx.BoxSizer(wx.VERTICAL)
        actions = wx.BoxSizer(wx.HORIZONTAL)
        actions.Add(section_bar(self, "GPS 지도"), 1, wx.ALIGN_CENTER_VERTICAL)
        self.zoom_buttons = []
        for label, command in (("＋", lambda: self.map.zoom_by(1)), ("−", lambda: self.map.zoom_by(-1))):
            control = button(self, label, command)
            # Spec fixes these as 48x48 DIP squares; the glyph does not grow with body text.
            control.SetMinSize(self.FromDIP((48, 48)))
            control.SetMaxSize(self.FromDIP((48, 48)))
            add(actions, control, border=3, flags=wx.ALL | wx.ALIGN_CENTER_VERTICAL)
            self.zoom_buttons.append(control)
        self.recenter_button = button(self, "현재 위치", self.recenter)
        add(actions, self.recenter_button, border=3, flags=wx.ALL | wx.ALIGN_CENTER_VERTICAL)
        # 버튼 옆 현재 위도·경도. 아래 readout보다 먼저 눈에 들어와야 해서 헤더 줄에 둔다.
        self.coordinates = text(self, "위도 —, 경도 —", 13, weight="semibold")
        add(actions, self.coordinates, border=6, flags=wx.ALL | wx.ALIGN_CENTER_VERTICAL)
        root.Add(actions, 0, wx.EXPAND | wx.ALL, self.FromDIP(10))
        frame = wx.Panel(self)
        stroke(frame)
        self.map = TileMapView(frame, self.pan, tiles)
        self.map.follow = True
        frame_sizer = wx.BoxSizer(wx.VERTICAL)
        frame_sizer.Add(self.map, 1, wx.EXPAND | wx.ALL, self.FromDIP(1))
        frame.SetSizer(frame_sizer)
        root.Add(frame, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, self.FromDIP(10))
        readout = wx.FlexGridSizer(4, self.FromDIP((12, 4)))
        readout.AddGrowableCol(1)
        readout.AddGrowableCol(3)
        self.status = text(self, "GPS 수신 대기", 14, weight="semibold")
        self.origin = text(self, "시작점 수신 대기", 13)
        for label, value in (("현재 위치", self.status), ("시작점", self.origin)):
            readout.Add(text(self, label, 13, colour=MUTED), 0, wx.ALIGN_TOP)
            readout.Add(value, 1, wx.EXPAND)
        root.Add(readout, 0, wx.EXPAND | wx.ALL, self.FromDIP(10))
        self.SetSizer(root)

    def pan(self):
        self.follow = False
        self.map.follow = False

    def recenter(self):
        self.follow = True
        self.map.follow = True
        if self.map.track:
            self.map.center = self.map.track[-1]
            self.map.Refresh(False)

    @staticmethod
    def coordinate_text(snapshot):
        """헤더 줄에 표시할 현재 위도·경도. 값이 없으면 0으로 채우지 않고 —로 둔다."""
        gps = snapshot.gps
        if gps and gps.latitude is not None:
            return f"위도 {format_coordinate(gps.latitude)}, 경도 {format_coordinate(gps.longitude)}"
        if snapshot.track:
            lat, lon = snapshot.track[-1]
            return f"위도 {format_coordinate(lat)}, 경도 {format_coordinate(lon)} (마지막 유효)"
        return "위도 —, 경도 —"

    def render(self, snapshot, map_failed=False, live=True):
        gps = snapshot.gps
        self.map.render(snapshot.track, self.follow, map_failed, valid=bool(live and gps and gps.latitude is not None))
        self.coordinates.SetLabel(self.coordinate_text(snapshot))
        if gps:
            if gps.latitude is not None:
                label = (f"{format_coordinate(gps.latitude)}, {format_coordinate(gps.longitude)}"
                         f"   {gps.fix_quality}") + ("   종료 시 위치" if not live else "")
            elif snapshot.track:
                lat, lon = snapshot.track[-1]
                label = f"fix 없음. 마지막 위치 {format_coordinate(lat)}, {format_coordinate(lon)}"
            else:
                label = "fix 없음. 유효 위치를 기다리는 중"
            shown_time = snapshot.last_valid_gps.received_time_utc if snapshot.last_valid_gps else gps.received_time_utc
            self.status.SetLabel(label + f"\n마지막 유효 위치 {shown_time[11:19]} UTC" if snapshot.last_valid_gps else label)
        if snapshot.start_gps:
            start = snapshot.start_gps
            movement = snapshot.movement
            movement_text = (f"\n직선거리 {movement[2]:.1f} m   동 {movement[0]:+.1f} m   북 {movement[1]:+.1f} m"
                             if movement else "")
            self.origin.SetLabel(f"{format_coordinate(start.latitude)}, "
                                 f"{format_coordinate(start.longitude)}{movement_text}")
        self.Layout()

    def dispose(self):
        self.map.dispose()
