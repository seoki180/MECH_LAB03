"""실험이 끝난 뒤 보는 결과 그래프 pane. 그래프 두 개를 나란히 둔다.

왼쪽: 시간/속도. 측정값(파랑)과 시험시나리오 목표값(빨강)을 같은 축에 겹쳐 그린다.
오른쪽: 시간/편차. 측정값 − 목표값. 0선을 점선으로 두어 부호를 읽게 한다.

pane은 계산하지 않는다. AnalysisService가 만든 ResultAnalysis를 render로 받아 그린다.
목표값이 없는 실행에서는 오른쪽 그래프에 이유를 적고 축을 지어내지 않는다.
"""
import wx

from experiment_app.ui.adapters.chart import ChartView, MEASURED, TARGET, DEVIATION
from experiment_app.ui.theme import (surface, text, section_bar, button,
                                     MUTED, INK)


def legend_entry(parent, colour, label):
    """색 견본과 이름. 색만으로 구분하지 않도록 이름을 함께 둔다."""
    row = wx.BoxSizer(wx.HORIZONTAL)
    swatch = wx.Panel(parent, size=parent.FromDIP((14, 14)))
    swatch.SetBackgroundColour(colour)
    row.Add(swatch, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, parent.FromDIP(6))
    row.Add(text(parent, label, 12, colour=INK), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, parent.FromDIP(14))
    return row


class ChartCard(wx.Panel):
    """제목 + 범례 + 그래프 하나."""

    def __init__(self, parent, title):
        super().__init__(parent)
        surface(self)
        root = wx.BoxSizer(wx.VERTICAL)
        heading = section_bar(self, title, 15)
        self.reset_button = button(heading, "전체 보기", lambda: self.chart.reset_zoom())
        self.reset_button.Enable(False)
        heading.GetSizer().Add(self.reset_button, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT,
                               self.FromDIP(8))
        heading.SetMinSize(self.FromDIP(wx.Size(-1, 56)))
        root.Add(heading, 0, wx.EXPAND | wx.ALL, self.FromDIP(10))
        self.legend = wx.BoxSizer(wx.HORIZONTAL)
        root.Add(self.legend, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, self.FromDIP(12))
        self.chart = ChartView(self)
        self.chart.on_zoom_changed = self.reset_button.Enable
        root.Add(self.chart, 1, wx.EXPAND | wx.ALL, self.FromDIP(10))
        self.axis = text(self, "", 12, colour=MUTED)
        root.Add(self.axis, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, self.FromDIP(12))
        self.SetSizer(root)

    def set_legend(self, entries):
        self.legend.Clear(delete_windows=True)
        for colour, label in entries:
            self.legend.Add(legend_entry(self, colour, label), 0, wx.ALIGN_CENTER_VERTICAL)
        self.Layout()


class ResultChartsPane(wx.Panel):
    """render(analysis)로 두 그래프를 갱신한다. 실험 창 본문에 교체되어 들어간다."""

    def __init__(self, parent):
        super().__init__(parent)
        self.SetBackgroundColour(parent.GetBackgroundColour())
        root = wx.BoxSizer(wx.VERTICAL)
        self.notes = text(self, "", 13, colour=MUTED)
        root.Add(self.notes, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, self.FromDIP(12))
        # 요구대로 그래프 둘만 좌우로 둔다. 좁아지면 위아래로 바꾼다.
        self.row = wx.BoxSizer(wx.HORIZONTAL)
        self.speed = ChartCard(self, "시간 / 속도")
        self.deviation = ChartCard(self, "시간 / 편차")
        self.row.Add(self.speed, 1, wx.EXPAND | wx.ALL, self.FromDIP(6))
        self.row.Add(self.deviation, 1, wx.EXPAND | wx.ALL, self.FromDIP(6))
        root.Add(self.row, 1, wx.EXPAND | wx.ALL, self.FromDIP(6))
        self.SetSizer(root)
        self.Bind(wx.EVT_SIZE, self.resize)

    def resize(self, event):
        # 좁은 화면에서 두 그래프가 모두 읽을 수 없게 눌리지 않도록 세로로 쌓는다.
        stacked = self.ToDIP(self.GetClientSize()).width < 760
        wanted = wx.VERTICAL if stacked else wx.HORIZONTAL
        if self.row.GetOrientation() != wanted:
            self.row.SetOrientation(wanted)
            self.Layout()
        event.Skip()

    def render(self, analysis):
        unit = analysis.measured.unit or ""
        self.speed.chart.render((analysis.measured, analysis.target),
                                empty_text="측정값이 없어 그래프를 그릴 수 없습니다")
        entries = [(MEASURED, f"측정값 · {analysis.measured.label}")]
        if analysis.target:
            entries.append((TARGET, "목표값 · 시험시나리오"))
        self.speed.set_legend(entries)
        self.speed.axis.SetLabel(f"가로 시간(초) · 세로 {unit or '값'}")

        self.deviation.chart.render((analysis.deviation,), zero_line=True,
                                    empty_text="목표값이 없어 편차를 그릴 수 없습니다")
        self.deviation.set_legend([(DEVIATION, "편차 · 측정 − 목표")])
        self.deviation.axis.SetLabel(f"가로 시간(초) · 세로 {unit or '값'} (0보다 크면 목표보다 빠름)")

        self.notes.SetLabel("  ".join(analysis.notes))
        self.notes.Show(bool(analysis.notes))
        self.Layout()

    def dispose(self):
        self.speed.chart.dispose()
        self.deviation.chart.dispose()
