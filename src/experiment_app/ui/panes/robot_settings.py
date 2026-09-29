"""Photo-style robot forms using the shared schema input contract."""
import wx
from experiment_app.ui.theme import text, input_control, scaled, CONTROL_HEIGHT, MUTED, DANGER
from .parameter_editor import ParameterEditorPane


class RobotSettingsPane(ParameterEditorPane):
    def __init__(self, parent, on_patch, section):
        super().__init__(parent, on_patch)
        self.section = section

    def create_field(self, schema, values, prefix, policy, errors):
        if schema.kind != "bool":
            return super().create_field(schema, values, prefix, policy, errors)
        path = prefix + schema.key
        editable = path in policy.editable_paths
        control = wx.CheckBox(self, label=schema.label)
        control.SetValue(bool(values.get(path)))
        control.SetBackgroundColour(self.GetBackgroundColour())
        input_control(control)
        control.Enable(editable)
        control.SetToolTip(schema.help if editable else policy.locked_reason)
        if editable:
            control.Bind(wx.EVT_CHECKBOX, lambda event: self.on_patch(path, control.GetValue()))
        self.controls[path] = control
        error = text(self, errors.get(path, ""), 12, colour=DANGER)
        self.error_labels[path] = error
        return control, error

    def render(self, schemas, values, prefix, policy, errors):
        self.GetSizer().Clear(True)
        self.controls, self.error_labels = {}, {}
        self.schemas = {schema.key: schema for schema in schemas}
        self.context = values, prefix, policy, errors
        root = self.GetSizer()
        root.Add(text(self, "Specification", 15, weight="semibold"), 0, wx.BOTTOM, self.FromDIP(12))
        self.row(root, "control", label_width=200)
        if self.section == "ar_trapezoidal_step":
            self.row(root, "use_br_for_speed_control")
            self.row(root, "use_gear_robot")
            for key in ("gear_mode", "start_delay", "start_level", "apply_rate", "amplitude",
                        "dwell_time", "return_rate", "end_delay", "no_of_cycles"):
                self.row(root, key, label_width=200)
        else:
            root.Add(text(self, "Straight Path", 15, weight="semibold"), 0,
                     wx.TOP | wx.BOTTOM, self.FromDIP(12))
            path = wx.WrapSizer(wx.HORIZONTAL)
            for keys in (("start_x", "start_y", "join_anywhere"),
                         ("distance", "angle", "allow_auto_heading_reversal"),
                         ("end_x", "end_y")):
                column = wx.BoxSizer(wx.VERTICAL)
                for key in keys:
                    self.row(column, key, label_width=72, input_width=112, short_option=True)
                path.Add(column, 0, wx.RIGHT | wx.BOTTOM, self.FromDIP(12))
            root.Add(path, 0, wx.EXPAND)
            root.Add(text(self, "Test Settings", 15, weight="semibold"), 0,
                     wx.TOP | wx.BOTTOM, self.FromDIP(12))
            for key in ("maximum_steering_wheel_amplitude", "maximum_steering_wheel_velocity",
                        "maximum_steering_wheel_acceleration"):
                self.row(root, key, label_width=280)
        self.Layout()

    def row(self, sizer, key, label_width=200, input_width=180, short_option=False):
        schema = self.schemas[key]
        control, error = self.create_field(schema, *self.context)
        block = wx.BoxSizer(wx.VERTICAL)
        row = wx.BoxSizer(wx.HORIZONTAL)
        if schema.kind == "bool":
            if short_option and key == "allow_auto_heading_reversal":
                control.SetLabel("Auto heading reversal")
            row.Add(control, 0, wx.ALIGN_CENTER_VERTICAL)
        else:
            label = text(self, schema.label, 13, colour=MUTED)
            label.Wrap(self.FromDIP(scaled(label_width)))
            label.SetMinSize(self.FromDIP((scaled(label_width), -1)))
            row.Add(label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, self.FromDIP(8))
            control.SetMinSize(self.FromDIP((scaled(input_width), CONTROL_HEIGHT)))
            control.SetMaxSize(self.FromDIP((scaled(input_width), -1)))
            row.Add(control, 0, wx.ALIGN_CENTER_VERTICAL)
            if schema.unit:
                row.Add(text(self, schema.unit, 12, colour=MUTED), 0,
                        wx.ALIGN_CENTER_VERTICAL | wx.LEFT, self.FromDIP(6))
        block.Add(row, 0, wx.EXPAND)
        block.Add(error, 0, wx.EXPAND)
        sizer.Add(block, 0, wx.EXPAND | wx.BOTTOM, self.FromDIP(6))
