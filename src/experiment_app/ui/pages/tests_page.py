import wx
from experiment_app.ui.theme import button, add, card, scaled, CONCRETE
from experiment_app.ui.panes.test_tree import TestTreePane
from experiment_app.ui.panes.test_details import TestDetailsPane


class TestsPage(wx.Panel):
    def __init__(self, parent, fields, on_select, on_reorder, on_patch,
                 file_commands=None):
        super().__init__(parent)
        self.SetBackgroundColour(CONCRETE)
        self.narrow, self.show_list = False, False
        root = wx.BoxSizer(wx.VERTICAL)
        self.toggle = button(self, "시험목록 보기", self.toggle_list)
        add(root, self.toggle, border=4)
        self.body = wx.BoxSizer(wx.HORIZONTAL)
        self.tree_card, self.tree = card(self, lambda parent: TestTreePane(parent, on_select, on_reorder))
        self.tree_card.SetMinSize(self.FromDIP((scaled(280), -1)))
        self.details_card, self.details = card(
            self, lambda parent: TestDetailsPane(parent, fields, on_patch, file_commands), scrollable=True)
        self.body.Add(self.tree_card, 3, wx.EXPAND | wx.RIGHT, self.FromDIP(12))
        self.body.Add(self.details_card, 7, wx.EXPAND)
        root.Add(self.body, 1, wx.EXPAND | wx.ALL, self.FromDIP(12))
        self.SetSizer(root)
        self.Bind(wx.EVT_SIZE, self._size)

    def toggle_list(self):
        self.show_list = not self.show_list
        self._layout()

    def selection_done(self):
        self.show_list = False
        self._layout()

    def _size(self, event):
        # Breakpoint stays in raw DIP: scaling it past the 1280 DIP reference size would
        # collapse the tablet layout to a single column on every screen.
        self.narrow = self.ToDIP(self.GetClientSize()).width < 1100
        self._layout()
        event.Skip()

    def _layout(self):
        self.toggle.Show(self.narrow)
        self.toggle.SetLabel("편집으로 돌아가기" if self.show_list else "시험목록 보기")
        self.toggle.SetSelected(self.show_list)
        self.tree_card.Show(not self.narrow or self.show_list)
        self.details_card.Show(not self.narrow or not self.show_list)
        self.Layout()

    def dispose(self):
        self.tree.dispose()
        self.details.dispose()
