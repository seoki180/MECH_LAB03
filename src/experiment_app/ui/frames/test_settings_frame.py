"""시험 설정 창. 아직 내용이 없는 빈 흰색 창이다.

버튼 자리와 창 수명만 먼저 만든다. 설정 항목이 정해지면 이 창에 pane을 붙인다.
"""
import wx


class TestSettingsFrame(wx.Frame):
    def __init__(self, parent, title="시험 설정"):
        super().__init__(parent, title=title)
        if wx.Platform == "__WXMSW__":
            self.SetDoubleBuffered(True)
        self.SetBackgroundColour(wx.Colour(255, 255, 255))
        self.SetClientSize(self.FromDIP((800, 600)))
        self.SetMinSize(self.FromDIP((480, 360)))
