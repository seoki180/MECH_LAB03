import wx
from wx.lib.scrolledpanel import ScrolledPanel
from experiment_app.ui.theme import text, button, add, surface, section_bar, chip, set_chip, MUTED
from .parameter_editor import ParameterEditorPane
from .specification import SpecificationPane


class TestDetailsPane(ScrolledPanel):
    def __init__(self, parent, fields, on_patch, on_structure):
        super().__init__(parent)
        surface(self)
        basic, steps = fields
        self.basic_schemas = basic
        root = wx.BoxSizer(wx.VERTICAL)
        title_row = wx.BoxSizer(wx.HORIZONTAL)
        self.heading = text(self, "시험을 선택하세요", 22, weight="semibold")
        title_row.Add(self.heading, 1, wx.ALIGN_CENTER_VERTICAL)
        self.mode = chip(self, "조회 중", "waiting")
        title_row.Add(self.mode, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, self.FromDIP(12))
        root.Add(title_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, self.FromDIP(20))
        self.summary = text(self, "왼쪽 시험목록에서 실험 설정을 선택하거나 추가하세요.", 13, colour=MUTED)
        root.Add(self.summary, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, self.FromDIP(20))
        self.facts = wx.BoxSizer(wx.HORIZONTAL)
        self.fact_values = {}
        for key, label in (("type", "종류"), ("revision", "리비전")):
            self.facts.Add(text(self, label, 13, colour=MUTED), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, self.FromDIP(8))
            self.fact_values[key] = text(self, "", 14, weight="semibold")
            self.facts.Add(self.fact_values[key], 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, self.FromDIP(28))
        self.edit_hint = text(self, "", 13, colour=MUTED)
        self.facts.Add(self.edit_hint, 1, wx.ALIGN_CENTER_VERTICAL)
        root.Add(self.facts, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, self.FromDIP(20))
        root.AddSpacer(self.FromDIP(20))
        self.basic_bar = section_bar(self, "기본 정보")
        root.Add(self.basic_bar, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, self.FromDIP(20))
        self.basic = ParameterEditorPane(self, on_patch, label_value=True)
        root.Add(self.basic, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, self.FromDIP(20))
        self.spec = SpecificationPane(self, steps, on_patch, on_structure)
        root.Add(self.spec, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, self.FromDIP(20))
        self.advanced_toggle = button(self, "고급 설정 펼치기", self.toggle_advanced, align_left=True)
        root.Add(self.advanced_toggle, 0, wx.EXPAND | wx.ALL, self.FromDIP(20))
        root.AddSpacer(self.FromDIP(20))
        self.expanded = False
        self.SetSizer(root)
        self.SetupScrolling(scroll_x=False, rate_y=16)
        self.render(None, {}, None, {})

    def toggle_advanced(self):
        self.expanded = not self.expanded
        self.spec.set_advanced_shown(self.expanded)
        self.advanced_toggle.SetSelected(self.expanded)
        self.advanced_toggle.SetLabel("고급 설정 접기" if self.expanded else "고급 설정 펼치기")
        self.Layout()
        self.FitInside()

    def render(self, definition, changes, policy, errors, group_label=None):
        valid = definition is not None
        for control in (self.basic_bar, self.basic, self.spec, self.advanced_toggle, self.mode):
            control.Show(valid)
        self.GetSizer().Show(self.facts, valid, recursive=True)
        self.summary.Show(not valid)
        self.spec.set_advanced_shown(valid and self.expanded)
        if not valid:
            self.heading.SetLabel(group_label or "시험을 선택하세요")
            self.summary.SetLabel("시험목록에서 시험을 선택하거나 추가하세요.")
        else:
            values = {**definition.fields(), **changes}
            self.heading.SetLabel(definition.name)
            editing = policy.context != "readonly"
            set_chip(self.mode, "편집 중" if editing else "조회 중", "active" if editing else "waiting")
            self.edit_hint.SetLabel("" if editing else "수정 버튼을 누르면 편집할 수 있습니다.")
            self.fact_values["type"].SetLabel(definition.type_id)
            self.fact_values["type"].SetToolTip("종류는 시험을 만든 뒤 바꿀 수 없습니다.")
            self.fact_values["revision"].SetLabel(str(definition.revision) if definition.revision else "저장 전")
            self.basic.render(self.basic_schemas, values, "", policy, errors)
            self.spec.render(definition, values, policy, errors)
        self.Layout()
        self.FitInside()

    def update_draft(self, values, errors):
        self.basic.show_errors(errors)
        self.spec.show_errors(errors)
        self.Layout()
        self.FitInside()

    def focus_error(self, errors):
        if not self.expanded and self.spec.has_advanced_error(errors):
            self.toggle_advanced()
        for editor in (self.basic, self.spec):
            if editor.focus_error(errors):
                break

    def dispose(self):
        self.basic.dispose()
        self.spec.dispose()
