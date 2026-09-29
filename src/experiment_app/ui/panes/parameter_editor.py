import wx
from experiment_app.ui.theme import (surface, text, input_control, scaled, CONTROL_HEIGHT,
                                     DANGER, INPUT_READONLY, INPUT_EDITABLE, SHEET, TEXT, MUTED)


class ParameterEditorPane(wx.Panel):
    def __init__(self, parent, on_patch, label_value=False):
        super().__init__(parent)
        surface(self)
        self.on_patch = on_patch
        self.label_value = label_value
        self.controls, self.error_labels = {}, {}
        self.SetSizer(wx.BoxSizer(wx.VERTICAL))

    def render(self, schemas, values, prefix, policy, errors):
        self.GetSizer().Clear(True)
        self.controls, self.error_labels = {}, {}
        for schema in schemas:
            control, error_label = self.create_field(schema, values, prefix, policy, errors)
            if self.label_value:
                self._add_label_value_row(schema.label, control, error_label, schema.unit,
                                          360 if schema.kind == "str" else 180)
            else:
                title = schema.label + (f"   [{schema.unit}]" if schema.unit else "")
                row = wx.BoxSizer(wx.VERTICAL)
                row.Add(text(self, title, 14, colour=MUTED, weight="semibold"), 0, wx.BOTTOM, self.FromDIP(4))
                row.Add(control, 0, wx.EXPAND)
                row.Add(error_label, 0, wx.EXPAND)
                self.GetSizer().Add(row, 0, wx.EXPAND | wx.BOTTOM, self.FromDIP(12))
        self.Layout()

    def create_field(self, schema, values, prefix, policy, errors):
        path = prefix + schema.key
        value = values.get(path, "")
        if schema.kind == "bool" and not schema.required:
            control = wx.Choice(self, choices=["미설정", "사용", "사용 안 함"])
            control.SetSelection(0 if value is None else (1 if value else 2))
            event_type = wx.EVT_CHOICE
            read_value = lambda c=control: (None, True, False)[c.GetSelection()]
        elif schema.kind == "bool":
            control = wx.CheckBox(self, label="사용")
            control.SetValue(bool(value))
            event_type = wx.EVT_CHECKBOX
            read_value = control.GetValue
        else:
            align = wx.TE_RIGHT if schema.kind in {"int", "float"} else 0
            control = wx.TextCtrl(self, value="" if value is None else str(value), style=wx.BORDER_SIMPLE | align)
            event_type = wx.EVT_TEXT
            read_value = control.GetValue
        input_control(control)
        editable = path in policy.editable_paths
        if isinstance(control, wx.TextCtrl):
            control.SetEditable(editable)
            control.SetBackgroundColour(INPUT_EDITABLE if editable else INPUT_READONLY)
            control.SetForegroundColour(TEXT)
        else:
            control.Enable(editable)
        control.SetToolTip(schema.help if path in policy.editable_paths else policy.locked_reason)
        if editable:
            control.Bind(event_type, lambda event, p=path, read=read_value: self.on_patch(p, read()))
        self.controls[path] = control
        error_label = text(self, errors.get(path, ""), 12, colour=DANGER)
        self.error_labels[path] = error_label
        return control, error_label

    def _add_label_value_row(self, title, control, error_label, unit="", input_width=180):
        row = wx.BoxSizer(wx.HORIZONTAL)
        label_panel = wx.Panel(self)
        label_panel.SetBackgroundColour(SHEET)
        label_panel.SetMinSize(self.FromDIP((scaled(200), CONTROL_HEIGHT)))
        label = text(label_panel, title, 14, colour=MUTED)
        label_box = wx.BoxSizer(wx.HORIZONTAL)
        label_box.Add(label, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, self.FromDIP(10))
        label_panel.SetSizer(label_box)
        row.Add(label_panel, 0, wx.EXPAND | wx.RIGHT, self.FromDIP(16))
        value_box = wx.BoxSizer(wx.VERTICAL)
        control.SetMinSize(self.FromDIP((scaled(input_width), CONTROL_HEIGHT)))
        control.SetMaxSize(self.FromDIP((scaled(input_width), CONTROL_HEIGHT)))
        field = wx.BoxSizer(wx.HORIZONTAL)
        field.Add(control, 0)
        # Unit sits to the right of the input as in the reference form; fixed width keeps inputs aligned.
        unit_label = text(self, unit, 13, colour=MUTED)
        unit_label.SetMinSize(self.FromDIP((scaled(56), -1)))
        field.Add(unit_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, self.FromDIP(8))
        value_box.Add(field, 0, wx.EXPAND)
        value_box.Add(error_label, 0, wx.EXPAND | wx.TOP, self.FromDIP(3))
        row.Add(value_box, 1, wx.EXPAND)
        self.GetSizer().Add(row, 0, wx.EXPAND | wx.BOTTOM, self.FromDIP(8))

    def show_errors(self, errors):
        for path, label in self.error_labels.items():
            label.SetLabel(errors.get(path, ""))
        self.Layout()

    def focus_error(self, errors):
        for path in errors:
            if path in self.controls:
                self.controls[path].SetFocus()
                return True
        return False

    def dispose(self):
        pass
