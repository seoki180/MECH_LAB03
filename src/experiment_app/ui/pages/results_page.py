import json
import wx
from experiment_app.ui.theme import text, add, input_control


class ResultsPage(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent)
        self.items = []
        root = wx.BoxSizer(wx.VERTICAL)
        add(root, text(self, "측정 결과 (데모 실행 이력)", 22, weight="semibold"), border=16)
        self.choice = wx.Choice(self)
        input_control(self.choice)
        self.choice.Bind(wx.EVT_CHOICE, lambda e: self._details())
        add(root, self.choice)
        self.details = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY)
        input_control(self.details, height=-1)
        add(root, self.details, 1, border=16)
        self.SetSizer(root)

    def render(self, items, session_id=None):
        selected = self.items[self.choice.GetSelection()]["session_id"] if self.items and self.choice.GetSelection() >= 0 else None
        self.items = items
        self.choice.Set([f'{r["snapshot"]["definition"]["name"]}   {r["end_time"][:19].replace("T", " ")}   {r["completeness"]}   세션 {r["session_id"][:8]}' for r in items])
        if items:
            self.choice.SetSelection(next((i for i, item in enumerate(items) if item["session_id"] == (session_id or selected)), 0))
        self._details()

    def _details(self):
        if not self.items:
            self.details.ChangeValue("아직 측정 결과가 없습니다. 시험을 선택해 실험을 시작하세요.")
            return
        result = self.items[self.choice.GetSelection()]
        self.details.ChangeValue(f'상태: {result["state"]}, {result["completeness"]}\n종료 원인: {result["end_reason"]}\n'
                                 f'원본 기록: {result["recording_path"]}\n\n실행 snapshot (읽기 전용)\n' +
                                 json.dumps(result["snapshot"], ensure_ascii=False, indent=2))
