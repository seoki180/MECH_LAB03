import wx
from wx.lib.scrolledpanel import ScrolledPanel
from experiment_app.ui.theme import surface, text, button, add, section_bar, font, input_control, MUTED


class TestTreePane(wx.Panel):
    def __init__(self, parent, on_select, on_reorder):
        super().__init__(parent)
        surface(self)
        self.on_select, self.on_reorder = on_select, on_reorder
        self.groups, self.definitions, self.selected = {}, [], None
        self.collapsed = set()
        root = wx.BoxSizer(wx.VERTICAL)
        root.Add(section_bar(self, "시험목록"), 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, self.FromDIP(12))
        self.search = wx.SearchCtrl(self)
        surface(self.search)
        self.search.SetDescriptiveText("이름이나 종류로 검색")
        input_control(self.search)
        self.search.Bind(wx.EVT_TEXT, lambda e: self._rows())
        add(root, self.search)
        self.rows = ScrolledPanel(self)
        surface(self.rows)
        self.rows.SetSizer(wx.BoxSizer(wx.VERTICAL))
        self.rows.SetupScrolling(scroll_x=False, rate_y=16)
        add(root, self.rows, 1, border=4)
        self.hint = text(self, "선택한 시험을 같은 시험목록 안에서 옮깁니다.", 12, colour=MUTED)
        add(root, self.hint)
        bottom = wx.BoxSizer(wx.HORIZONTAL)
        self.up = button(self, "위로", lambda: on_reorder(-1))
        self.down = button(self, "아래로", lambda: on_reorder(1))
        add(bottom, self.up, 1, border=4)
        add(bottom, self.down, 1, border=4)
        root.Add(bottom, 0, wx.EXPAND)
        self.SetSizer(root)

    def render(self, groups, definitions, selected):
        self.groups, self.definitions, self.selected = groups, definitions, selected
        self._rows()

    def _rows(self):
        self.rows.Freeze()
        self.rows.GetSizer().Clear(True)
        query = self.search.GetValue().casefold().strip()
        for group_id, name in self.groups.items():
            tests = [t for t in self.definitions if t.group_id == group_id and
                     (query in t.name.casefold() or query in t.type_id.casefold())]
            if query and not tests:
                continue
            row = wx.BoxSizer(wx.HORIZONTAL)
            toggle = button(self.rows, "▸" if group_id in self.collapsed else "▾", lambda g=group_id: self._toggle(g), tone="row")
            row.Add(toggle, 0, wx.ALL, self.FromDIP(2))
            group = button(self.rows, f"{name}   {len(tests)}", lambda g=group_id: self.on_select("group", g),
                           align_left=True, tone="row")
            group.SetFont(font(14, "bold"))
            group.SetSelected(self.selected == group_id)
            row.Add(group, 1, wx.EXPAND | wx.ALL, self.FromDIP(2))
            self.rows.GetSizer().Add(row, 0, wx.EXPAND | wx.TOP, self.FromDIP(6))
            if group_id in self.collapsed and not query:
                continue
            for definition in tests:
                selected = self.selected == definition.id
                control = button(self.rows, definition.name,
                                 lambda t=definition.id: self.on_select("test", t), align_left=True, tone="row")
                control.SetSelected(selected)
                indent = wx.BoxSizer(wx.HORIZONTAL)
                indent.Add(control, 1, wx.EXPAND | wx.LEFT, self.FromDIP(52))
                self.rows.GetSizer().Add(indent, 0, wx.EXPAND | wx.TOP | wx.BOTTOM | wx.RIGHT, self.FromDIP(2))
        allowed = not query and any(d.id == self.selected for d in self.definitions)
        self.up.Enable(allowed)
        self.down.Enable(allowed)
        self.hint.SetLabel("검색을 지우면 순서를 바꿀 수 있습니다." if query else "선택한 시험을 같은 시험목록 안에서 옮깁니다.")
        self.rows.Layout()
        self.rows.FitInside()
        self.rows.Thaw()

    def _toggle(self, group_id):
        if group_id in self.collapsed:
            self.collapsed.remove(group_id)
        else:
            self.collapsed.add(group_id)
        self._rows()

    def dispose(self):
        pass
