import wx
from experiment_app.ui.theme import style, button, add, scaled
from experiment_app.ui.pages.results_page import ResultsPage


class ResultsDialog(wx.Dialog):
    def __init__(self, parent, items, session_id):
        super().__init__(parent, title="측정 결과", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        style(self)
        self.SetClientSize(self.FromDIP((scaled(760), scaled(560))))
        self.SetMinSize(self.FromDIP((scaled(420), scaled(360))))
        root = wx.BoxSizer(wx.VERTICAL)
        self.results = ResultsPage(self)
        self.results.render(items, session_id)
        root.Add(self.results, 1, wx.EXPAND)
        add(root, button(self, "닫기", lambda: self.EndModal(wx.ID_CLOSE)), border=12)
        self.SetSizer(root)
        self.CentreOnParent()
