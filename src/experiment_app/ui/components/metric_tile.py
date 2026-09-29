import wx
from experiment_app.ui.theme import text, add, scaled, SHEET, MUTED, STOP, INK, OK, WARN, RULE

# quality label -> status strip colour; anything unlisted (e.g. 수신 대기) stays neutral.
STRIP = {"정상": OK, "지연": WARN, "연결 끊김": STOP, "값 오류": STOP}


class MetricTile(wx.Panel):
    """Instrument readout: a status strip, the value large and right-aligned, then quality and time."""
    def __init__(self, parent):
        super().__init__(parent)
        self.SetBackgroundColour(SHEET)
        root = wx.BoxSizer(wx.VERTICAL)
        self.strip = wx.Panel(self, size=self.FromDIP((-1, 6)))
        self.strip.SetBackgroundColour(RULE)
        root.Add(self.strip, 0, wx.EXPAND)
        self.label = text(self, "측정 값", 14, colour=MUTED, weight="semibold")
        root.Add(self.label, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, self.FromDIP(12))
        reading = wx.BoxSizer(wx.HORIZONTAL)
        reading.AddStretchSpacer()
        self.value = text(self, "—", 40, weight="bold")
        # Proportional digits: reserve width so the readout does not jump as values change.
        self.value.SetMinSize(self.FromDIP((scaled(180), -1)))
        self.value.SetWindowStyle(wx.ALIGN_RIGHT | wx.ST_NO_AUTORESIZE)
        reading.Add(self.value, 0, wx.ALIGN_BOTTOM)
        self.unit = text(self, "", 16, colour=MUTED, weight="semibold")
        self.unit.SetMinSize(self.FromDIP((scaled(44), -1)))
        reading.Add(self.unit, 0, wx.ALIGN_BOTTOM | wx.LEFT | wx.BOTTOM, self.FromDIP(8))
        root.Add(reading, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, self.FromDIP(12))
        details = wx.BoxSizer(wx.HORIZONTAL)
        self.quality = text(self, "수신 대기", 13, weight="semibold")
        self.updated = text(self, "—", 12, colour=MUTED)
        details.Add(self.quality, 1, wx.ALIGN_CENTER_VERTICAL)
        details.Add(self.updated, 0, wx.ALIGN_CENTER_VERTICAL)
        root.Add(details, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, self.FromDIP(12))
        self.SetSizer(root)

    def render(self, model):
        quality = model.quality_label
        self.label.SetLabel(model.label)
        self.value.SetLabel(model.value_text)
        self.unit.SetLabel(model.unit)
        self.quality.SetLabel(quality + (f", {model.age:.1f}초 전" if quality in {"지연", "연결 끊김"} else ""))
        self.value.SetForegroundColour(MUTED if quality == "연결 끊김" else INK)
        self.quality.SetForegroundColour(STOP if quality in {"값 오류", "연결 끊김"} else INK)
        colour = STRIP.get(quality, RULE)
        if self.strip.GetBackgroundColour() != wx.Colour(colour):
            self.strip.SetBackgroundColour(colour)
            self.strip.Refresh()
        self.updated.SetLabel(model.last_updated)
        self.Layout()
