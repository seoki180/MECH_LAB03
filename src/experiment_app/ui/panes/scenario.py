"""시험시나리오 표시와 CSV 가져오기/비우기.

시험 정의(test.json)는 로봇에 한 번 보내는 설정이고, 시나리오(target.csv)는 시간에
따른 목표값 열이다. 둘은 같은 시험 폴더 안에 있으며 시나리오는 시험당 하나다.
**시나리오는 선택이다** — 없으면 목표값 없이 실행하고, 파일이 있는데 읽지 못할 때만
실행을 막는다. 시험 폴더 자체의 가져오기/내보내기는 시험 이름 옆(TestDetailsPane)에
있다. 이 pane은 상태 표시와 명령 전달만 하고 파일을 직접 읽거나 쓰지 않는다.
"""
import wx
from experiment_app.ui.theme import (surface, text, button, chip, set_chip, section_bar,
                                     MUTED)

# 시나리오 상태 → 칩 색. 없음과 오류를 같은 색으로 묶지 않는다.
TONES = {"ready": "ok", "none": "waiting", "error": "error"}


class ScenarioPane(wx.Panel):
    """render(view_model)로 상태를 받고, 명령은 command callback으로 올린다."""

    def __init__(self, parent, on_import, on_clear):
        super().__init__(parent)
        surface(self)
        root = wx.BoxSizer(wx.VERTICAL)
        root.Add(section_bar(self, "시험시나리오"), 0, wx.EXPAND)
        status = wx.BoxSizer(wx.HORIZONTAL)
        self.state = chip(self, "시나리오 없음", "waiting")
        status.Add(self.state, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, self.FromDIP(12))
        self.file_name = text(self, "", 13, colour=MUTED)
        status.Add(self.file_name, 1, wx.ALIGN_CENTER_VERTICAL)
        root.Add(status, 0, wx.EXPAND | wx.TOP, self.FromDIP(12))
        self.detail = text(self, "", 13, colour=MUTED)
        root.Add(self.detail, 0, wx.EXPAND | wx.TOP, self.FromDIP(6))
        commands = wx.BoxSizer(wx.HORIZONTAL)
        self.buttons = {
            "import": button(self, "시나리오 CSV 가져오기", on_import),
            "clear": button(self, "시나리오 비우기", on_clear),
        }
        for key, control in self.buttons.items():
            commands.Add(control, 1, wx.EXPAND | (wx.LEFT if key != "import" else 0), self.FromDIP(8))
        root.Add(commands, 0, wx.EXPAND | wx.TOP, self.FromDIP(12))
        self.SetSizer(root)

    def render(self, model, enabled=True):
        """model은 presentation.view_models.ScenarioViewModel 또는 None(저장 전 시험)."""
        if model is None:
            set_chip(self.state, "저장 후 사용", "waiting")
            self.file_name.SetLabel("")
            self.detail.SetLabel("시험을 저장하면 시나리오를 가져올 수 있습니다.")
            for control in self.buttons.values():
                control.Enable(False)
            self.Layout()
            return
        set_chip(self.state, model.summary, TONES.get(model.state, "waiting"))
        self.file_name.SetLabel(model.file_name)
        self.detail.SetLabel(model.detail)
        self.buttons["import"].Enable(enabled)
        self.buttons["clear"].Enable(enabled and model.state != "none")
        self.Layout()

    def dispose(self):
        pass
