import wx
from experiment_app.demo.fixtures import DATA_FIELDS
from experiment_app.domain.robot_settings import ROBOT_FIELDS
from experiment_app.ui.theme import surface, text, section_bar, RULE
from .parameter_editor import ParameterEditorPane
from .robot_settings import RobotSettingsPane

# Sections shown as soon as a test is selected; everything else waits behind the
# advanced toggle so the most often edited angles stay on the first screen.
PRIMARY_SECTIONS = ("point_angle",)


class SpecificationPane(wx.Panel):
    """Existing input and two independent robot settings share schema-based editors."""

    def __init__(self, parent, on_patch):
        super().__init__(parent)
        surface(self)
        root = wx.BoxSizer(wx.VERTICAL)
        rule = wx.Panel(self, size=self.FromDIP((-1, 1)))
        rule.SetBackgroundColour(RULE)
        root.Add(rule, 0, wx.EXPAND | wx.TOP, self.FromDIP(12))
        root.Add(text(self, "실험 입력 데이터", 18, weight="bold"), 0, wx.TOP | wx.BOTTOM, self.FromDIP(20))
        self.editors = []
        self.advanced_paths = set()
        # Advanced sections live on their own panel so one Show() hides headings,
        # editors and their spacing together.
        self.advanced = wx.Panel(self)
        surface(self.advanced)
        advanced_sizer = wx.BoxSizer(wx.VERTICAL)
        for section, label in (("point_angle", "Point Angle"),
                               ("calibration_data", "Calibration Data"),
                               ("limit_point", "Limit Point"),
                               ("zero_brake_angle", "Zero Brake Angle")):
            primary = section in PRIMARY_SECTIONS
            host, sizer = (self, root) if primary else (self.advanced, advanced_sizer)
            editor = self._section(host, sizer, label,
                                   lambda body: ParameterEditorPane(body, on_patch, label_value=True))
            prefix = "data/" if section == "zero_brake_angle" else f"data/{section}/"
            self.editors.append((editor, DATA_FIELDS[section], prefix))
            if not primary:
                self.advanced_paths.update(prefix + schema.key for schema in DATA_FIELDS[section])
        for section, label in (("ar_trapezoidal_step", "AR Trapezoidal Step"),
                               ("pf_straight_line", "PF Straight Line")):
            editor = self._section(self.advanced, advanced_sizer, label + " 로봇 설정",
                                   lambda body, s=section: RobotSettingsPane(body, on_patch, s))
            prefix = f"robot/{section}/"
            self.editors.append((editor, ROBOT_FIELDS[section], prefix))
            self.advanced_paths.update(prefix + schema.key for schema in ROBOT_FIELDS[section])
        self.advanced.SetSizer(advanced_sizer)
        self.advanced.Hide()
        root.Add(self.advanced, 0, wx.EXPAND)
        self.SetSizer(root)

    def _section(self, host, sizer, label, make_editor):
        """Ruled heading over its editor; sections are separated by space, not boxes."""
        sizer.Add(section_bar(host, label), 0, wx.EXPAND)
        editor = make_editor(host)
        sizer.Add(editor, 0, wx.EXPAND | wx.TOP | wx.BOTTOM, host.FromDIP(16))
        sizer.AddSpacer(host.FromDIP(8))
        return editor

    def set_advanced_shown(self, shown):
        """Show or hide every section behind the advanced toggle."""
        if self.advanced.IsShown() == bool(shown):
            return
        self.advanced.Show(bool(shown))
        self.Layout()

    def advanced_shown(self):
        return self.advanced.IsShown()

    def has_advanced_error(self, errors):
        """True when a validation error sits in a section the advanced toggle hides."""
        return any(path in self.advanced_paths for path in errors)

    def render(self, definition, values, policy, errors):
        for editor, schemas, prefix in self.editors:
            editor.render(schemas, values, prefix, policy, errors)
        self.Layout()

    def show_errors(self, errors):
        for editor, _, _ in self.editors:
            editor.show_errors(errors)

    def focus_error(self, errors):
        return any(editor.focus_error(errors) for editor, _, _ in self.editors)

    def dispose(self):
        for editor, _, _ in self.editors:
            editor.dispose()
