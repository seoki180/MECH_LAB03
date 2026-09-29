import wx
from experiment_app.ui.components.metric_tile import MetricTile
from experiment_app.ui.theme import BACKGROUND, add


class SensorPartPane(wx.Panel):
    """A row of reusable metric cards, stacked on narrow screens."""
    def __init__(self, parent, part_id, channels):
        super().__init__(parent)
        self.SetBackgroundColour(BACKGROUND)
        self.part_id = part_id
        self.cards = {channel: MetricTile(self) for part, channel, _, _ in channels if part == part_id}
        self.row = wx.BoxSizer(wx.HORIZONTAL)
        for tile in self.cards.values():
            add(self.row, tile, 1, border=6)
        self.SetSizer(self.row)

    def render(self, models):
        for channel, tile in self.cards.items():
            tile.render(models[channel])

    def set_stacked(self, stacked):
        self.row.SetOrientation(wx.VERTICAL if stacked else wx.HORIZONTAL)
        self.Layout()

    def dispose(self):
        pass
