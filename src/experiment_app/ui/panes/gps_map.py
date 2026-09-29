import wx
from experiment_app.ui.adapters.tile_map import TileMapView
from experiment_app.ui.theme import text, button, add, stroke, section_bar, SHEET, MUTED


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

    def render(self, snapshot, map_failed=False, live=True):
        gps = snapshot.gps
        self.map.render(snapshot.track, self.follow, map_failed, valid=bool(live and gps and gps.latitude is not None))
        if gps:
            if gps.latitude is not None:
                label = f"{gps.latitude:.6f}, {gps.longitude:.6f}   {gps.fix_quality}" + ("   종료 시 위치" if not live else "")
            elif snapshot.track:
                lat, lon = snapshot.track[-1]
                label = f"fix 없음. 마지막 위치 {lat:.6f}, {lon:.6f}"
            else:
                label = "fix 없음. 유효 위치를 기다리는 중"
            shown_time = snapshot.last_valid_gps.received_time_utc if snapshot.last_valid_gps else gps.received_time_utc
            self.status.SetLabel(label + f"\n마지막 유효 위치 {shown_time[11:19]} UTC" if snapshot.last_valid_gps else label)
        if snapshot.start_gps:
            start = snapshot.start_gps
            movement = snapshot.movement
            movement_text = (f"\n직선거리 {movement[2]:.1f} m   동 {movement[0]:+.1f} m   북 {movement[1]:+.1f} m"
                             if movement else "")
            self.origin.SetLabel(f"{start.latitude:.6f}, {start.longitude:.6f}{movement_text}")
        self.Layout()

    def dispose(self):
        self.map.dispose()
