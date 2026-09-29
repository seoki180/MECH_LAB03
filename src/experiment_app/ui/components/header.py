import wx
from experiment_app.ui import icons
from experiment_app.ui.theme import (text, button, chip, set_chip, font, add,
                                     GRAPHITE, GRAPHITE_RULE, ON_GRAPHITE, WARN)

# Command bar geometry stays in raw DIP: 72/36 already clear the scaled label and icon,
# and growing it would push the wrapped command rows off a 768 DIP screen.
COMMAND_HEIGHT = 72
LOGO_HEIGHT = 36  # DIP
LOGOS = ("KTL_ci_logo-1.png", "SPLT_logo수정q.png")


class Header(wx.Panel):
    """Persistent graphite tool strip, separately instantiated in both frames.

    commands: (key, label, callback, tone) tuples; tone is True (primary), False (plain) or a
    BoxButton tone name. A None entry draws a group divider.
    """
    def __init__(self, parent, title, commands, navigation=(), *, compact=False, status_chips=True):
        super().__init__(parent)
        self.SetBackgroundColour(GRAPHITE)
        root = wx.BoxSizer(wx.VERTICAL)
        first = wx.WrapSizer(wx.HORIZONTAL, wx.REMOVE_LEADING_SPACES)
        self.title = text(self, title, 20, colour=ON_GRAPHITE, weight="semibold")
        add(first, self.title, border=12, flags=wx.LEFT | wx.RIGHT | wx.TOP | wx.ALIGN_CENTER_VERTICAL)
        self.navigation = {}
        for key, label, callback in navigation:
            widget = button(self, label, callback, tone="header")
            self.navigation[key] = widget
            add(first, widget, border=4)
        if compact:
            self.title.Hide()
        else:
            root.Add(first, 0, wx.EXPAND)
        row = wx.BoxSizer(wx.HORIZONTAL)
        actions = wx.WrapSizer(wx.HORIZONTAL, wx.REMOVE_LEADING_SPACES)
        self.buttons = {}
        for command in commands:
            if command is None:
                divider = wx.Panel(self, size=self.FromDIP((1, COMMAND_HEIGHT - 24)))
                divider.SetBackgroundColour(GRAPHITE_RULE)
                actions.Add(divider, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, self.FromDIP(8))
                continue
            key, label, callback, tone = command
            tone = {True: "primary", False: "header"}.get(tone, tone)
            widget = button(self, label, callback, height=COMMAND_HEIGHT, icon=icons.load(key), tone=tone)
            widget.SetFont(font(15, "semibold" if tone != "header" else "regular"))
            self.buttons[key] = widget
            actions.Add(widget, 0, wx.ALL, self.FromDIP(4))
        row.Add(actions, 1, wx.EXPAND | wx.ALL, self.FromDIP(6))
        status = wx.BoxSizer(wx.HORIZONTAL)
        brand = self._brand()
        if brand:
            status.Add(brand, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, self.FromDIP(12))
        self.demo = chip(self, "데모", "idle", 12)
        self.demo.label.SetForegroundColour(WARN)
        self.state = chip(self, "준비", "idle")
        self.clock = text(self, "", 28, colour=ON_GRAPHITE, weight="bold")
        status.Add(self.demo, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, self.FromDIP(6))
        status.Add(self.state, 0, wx.ALIGN_CENTER_VERTICAL)
        status.Add(self.clock, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, self.FromDIP(16))
        if not status_chips:
            self.demo.Hide()
            self.state.Hide()
        row.Add(status, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT | wx.LEFT, self.FromDIP(16))
        root.Add(row, 0, wx.EXPAND)
        self.SetSizer(root)

    def _brand(self):
        """Service logos side by side, drawn straight on the graphite strip."""
        bundles = [b for b in (icons.logo(name, LOGO_HEIGHT) for name in LOGOS) if b]
        if not bundles:
            return None
        sizer = wx.BoxSizer(wx.HORIZONTAL)
        for i, bundle in enumerate(bundles):
            sizer.Add(wx.StaticBitmap(self, bitmap=bundle), 0,
                      wx.ALIGN_CENTER_VERTICAL | (wx.LEFT if i else 0), self.FromDIP(12))
        return sizer

    def render(self, status, tone="idle", clock=None, active=None):
        set_chip(self.state, status, tone)
        # Keep the demo tag readable on its graphite chip after any recolour.
        self.demo.label.SetForegroundColour(WARN)
        shown = clock is not None
        if self.clock.IsShown() != shown or (shown and self.clock.GetLabel() != clock):
            self.clock.SetLabel(clock or "")
            self.clock.Show(shown)
            self.Layout()
        for key, widget in self.navigation.items():
            widget.SetSelected(key == active)

    def dispose(self):
        pass
