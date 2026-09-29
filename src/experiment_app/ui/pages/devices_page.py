import wx
from pathlib import Path
from experiment_app.ui.theme import text, button, add, surface, section_bar, input_control, MUTED


class DevicesPage(wx.Panel):
    def __init__(self, parent, scenarios, on_scenario, on_nmea, nmea_path):
        super().__init__(parent)
        surface(self)
        root = wx.BoxSizer(wx.VERTICAL)
        add(root, text(self, "장치 연결", 22, weight="semibold"), border=24)
        facts = wx.FlexGridSizer(2, self.FromDIP((20, 10)))
        facts.AddGrowableCol(1)
        self.file_label = text(self, Path(nmea_path).name if nmea_path else "선택되지 않음", 14, weight="semibold")
        if nmea_path:
            self.file_label.SetToolTip(str(nmea_path))
        for label, value in (("센서와 GPS", text(self, "NMEA 파일 재생", 14)),
                             ("로봇", text(self, "데모 연결", 14)),
                             ("NMEA 파일", self.file_label),
                             ("A 영역", text(self, "속도, 시작점 거리", 14)),
                             ("B 영역", text(self, "횡방향, 종방향", 14)),
                             ("C 영역", text(self, "현재 속도, 누적 이동거리", 14)),
                             ("기록", text(self, "화면 10 Hz, 최대 30초 재생, 원본 JSONL 저장", 14))):
            facts.Add(text(self, label, 13, colour=MUTED), 0, wx.ALIGN_CENTER_VERTICAL)
            facts.Add(value, 1, wx.EXPAND)
        root.Add(facts, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, self.FromDIP(24))
        add(root, button(self, "NMEA 파일 선택", lambda: self.select_nmea(on_nmea)), border=24,
            flags=wx.ALL | wx.ALIGN_LEFT)
        root.Add(section_bar(self, "실패 상태 시나리오"), 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, self.FromDIP(16))
        self.scenario = wx.Choice(self, choices=list(scenarios))
        self.scenario.SetSelection(0)
        input_control(self.scenario)
        self.scenario.Bind(wx.EVT_CHOICE, lambda e: on_scenario(self.scenario.GetStringSelection()))
        add(root, self.scenario, border=16)
        add(root, text(self, "수집 시나리오는 다음 시작부터, 저장 실패는 바로 적용됩니다.", 12, colour=MUTED), border=16)
        self.SetSizer(root)

    def select_nmea(self, on_nmea):
        with wx.FileDialog(self, "NMEA 파일 선택", wildcard="NMEA 파일 (*.nmea)|*.nmea|모든 파일|*.*",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
            if dialog.ShowModal() == wx.ID_OK and on_nmea(dialog.GetPath()):
                self.file_label.SetLabel(Path(dialog.GetPath()).name)
                self.file_label.SetToolTip(dialog.GetPath())
                self.Layout()
