"""Field-instrument tokens: a graphite tool strip over a concrete work surface.

Colour is reserved for state and primary commands; everything else is ink on sheet.
"""
import ctypes
import ctypes.util
import sys
import wx
from experiment_app.paths import asset_dir

GRAPHITE = "#262B30"
GRAPHITE_RAISED = "#353B41"
GRAPHITE_RULE = "#4A5158"
ON_GRAPHITE = "#EEF0F1"
CONCRETE = "#E4E6E3"
SHEET = "#FFFFFF"
INK = "#1C2126"
MUTED = "#5E6770"
RULE = "#C9CDCF"
SIGNAL = "#1F6FD1"
SIGNAL_PRESSED = "#1757A8"
SIGNAL_TINT = "#E3EEFB"
WARN = "#F2B01E"
STOP = "#C8322B"
OK = "#2F8A55"
INPUT_READONLY = "#F1F2F0"
INPUT_EDITABLE = SHEET

# Legacy names kept for callers that only need the role.
BACKGROUND = CONCRETE
SURFACE = SHEET
TEXT = INK
DANGER = STOP

# State tone -> (fill, label) for chips and status strips.
TONES = {
    "idle": (GRAPHITE_RAISED, ON_GRAPHITE),
    "active": (SIGNAL, "#FFFFFF"),
    "ok": (OK, "#FFFFFF"),
    "error": (STOP, "#FFFFFF"),
    "waiting": (RULE, INK),
}

# tone -> state -> (background, label, border); border None means no outline.
BUTTON_COLOURS = {
    "sheet": {
        "normal": ("#F3F4F2", INK, RULE),
        "hover": ("#E7E9E6", INK, "#A9AFB3"),
        "pressed": (INK, "#FFFFFF", INK),
        "selected": (SIGNAL_TINT, INK, SIGNAL_TINT),
        "disabled": ("#EEEFED", "#9AA1A7", "#DADDDB"),
        "disabled_selected": ("#E9EEF4", "#6B7480", "#E9EEF4"),
    },
    "row": {
        "normal": (SHEET, INK, None),
        "hover": ("#F1F2F0", INK, None),
        "pressed": (CONCRETE, INK, None),
        "selected": (SIGNAL_TINT, INK, None),
        "disabled": (SHEET, "#9AA1A7", None),
        "disabled_selected": ("#F2F5F9", "#6B7480", None),
    },
    "header": {
        "normal": (GRAPHITE_RAISED, ON_GRAPHITE, None),
        "hover": ("#434A51", "#FFFFFF", None),
        "pressed": ("#15181B", "#FFFFFF", None),
        "selected": (SIGNAL, "#FFFFFF", None),
        "disabled": (GRAPHITE, "#6C747B", None),
        "disabled_selected": (GRAPHITE, "#6C747B", None),
    },
    "primary": {
        "normal": (SIGNAL, "#FFFFFF", None),
        "hover": ("#3A83DE", "#FFFFFF", None),
        "pressed": (SIGNAL_PRESSED, "#FFFFFF", None),
        "selected": (SIGNAL_PRESSED, "#FFFFFF", None),
        "disabled": (GRAPHITE_RAISED, "#7E868D", None),
        "disabled_selected": (GRAPHITE_RAISED, "#7E868D", None),
    },
    "danger": {
        "normal": (STOP, "#FFFFFF", None),
        "hover": ("#DA4239", "#FFFFFF", None),
        "pressed": ("#9E2620", "#FFFFFF", None),
        "selected": ("#9E2620", "#FFFFFF", None),
        "disabled": (GRAPHITE_RAISED, "#7E868D", None),
        "disabled_selected": (GRAPHITE_RAISED, "#7E868D", None),
    },
}

FONT_DIR = asset_dir("fonts")
FONT_FAMILY = "Pretendard"
WEIGHTS = {"regular": wx.FONTWEIGHT_NORMAL, "semibold": wx.FONTWEIGHT_SEMIBOLD, "bold": wx.FONTWEIGHT_BOLD}
_face = None

# Single knob for overall legibility on the tablet. Call sites keep their relative
# sizes; this multiplies every point size and the text-driven DIP boxes that hold them.
# 1.15 is the largest value where the experiment frame's four metric cards and the
# command bar still fit a 1024x768 DIP screen without the last card falling below the fold.
FONT_SCALE = 1.15
# Standard touch target height for inputs and list buttons, sized for the scaled text.
CONTROL_HEIGHT = 56
# Windows nonclient scrollbar dimensions; never change global OS metrics.
SCROLLBAR_WIDTH = 48
SCROLLBAR_THUMB_MIN = 48


def scaled(dip):
    """Grow a text-driven DIP length with FONT_SCALE; -1 (automatic) stays automatic."""
    return dip if dip < 0 else int(round(dip * FONT_SCALE))


def _register_mac(path):
    """wx on macOS only loads fonts from an app bundle; register for this process via CoreText."""
    cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
    ct = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreText"))
    cf.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
    cf.CFURLCreateFromFileSystemRepresentation.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_bool]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    ct.CTFontManagerRegisterFontsForURL.restype = ctypes.c_bool
    ct.CTFontManagerRegisterFontsForURL.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
    raw = str(path).encode()
    url = cf.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), False)
    if url:
        ct.CTFontManagerRegisterFontsForURL(url, 1, None)  # kCTFontManagerScopeProcess
        cf.CFRelease(url)


def load_fonts():
    """Register the bundled Pretendard files; fall back to the system face if unavailable."""
    global _face
    # Resolved here rather than at import so a frozen run sees the unpacked bundle dir.
    for path in sorted(asset_dir("fonts").glob(f"{FONT_FAMILY}-*.otf")):
        try:
            if sys.platform == "darwin":
                _register_mac(path)
            else:
                with wx.LogNull():
                    wx.Font.AddPrivateFont(str(path))
        except OSError:
            pass
    _face = FONT_FAMILY if wx.FontEnumerator.IsValidFacename(FONT_FAMILY) else None


def font(size=14, weight="regular"):
    info = wx.FontInfo(max(1, int(round(size * FONT_SCALE)))).Family(wx.FONTFAMILY_SWISS).Weight(WEIGHTS[weight])
    if _face:
        info.FaceName(_face)
    return wx.Font(info)


def input_control(control, height=CONTROL_HEIGHT, size=14, weight="regular"):
    """Native controls ignore the frame font; give them the scaled face and touch height."""
    control.SetFont(font(size, weight))
    control.SetMinSize(control.FromDIP((-1, height)))
    return control


def style(window):
    window.SetBackgroundColour(CONCRETE)
    window.SetForegroundColour(INK)
    window.SetFont(font(14))


def surface(window):
    window.SetBackgroundColour(SHEET)
    window.SetForegroundColour(INK)


def text(parent, label, size=14, bold=False, colour=INK, weight=None):
    widget = wx.StaticText(parent, label=label)
    widget.SetFont(font(size, weight or ("bold" if bold else "regular")))
    widget.SetForegroundColour(colour)
    widget.SetBackgroundColour(parent.GetBackgroundColour())
    return widget


def button(parent, label, callback, primary=False, height=48, icon=None, align_left=False, tone=None):
    from experiment_app.ui.components.box_button import BoxButton
    widget = BoxButton(parent, label, primary, height, icon, align_left, tone)
    widget.Bind(wx.EVT_BUTTON, lambda event: callback())
    return widget


def add(sizer, widget, proportion=0, border=8, flags=wx.EXPAND | wx.ALL):
    sizer.Add(widget, proportion, flags, widget.FromDIP(border))


def stroke(panel, colour=RULE):
    """Paint a 1 DIP border around the panel; children should leave a 1 DIP margin."""
    def paint(event):
        dc = wx.PaintDC(panel)
        dc.SetPen(wx.Pen(colour, panel.FromDIP(1)))
        dc.SetBrush(wx.TRANSPARENT_BRUSH)
        width, height = panel.GetClientSize()
        dc.DrawRectangle(0, 0, width, height)
    panel.Bind(wx.EVT_PAINT, paint)
    panel.Bind(wx.EVT_SIZE, lambda event: (panel.Refresh(False), event.Skip()))


def card(parent, content_factory):
    """Borderless sheet on the concrete surface; content_factory(card) builds the single child."""
    panel = wx.Panel(parent)
    surface(panel)
    content = content_factory(panel)
    sizer = wx.BoxSizer(wx.VERTICAL)
    sizer.Add(content, 1, wx.EXPAND)
    panel.SetSizer(sizer)
    return panel, content


def section_bar(parent, label, size=16):
    """Section title: a 4 DIP ink bar at the left edge of a bold heading."""
    bar = wx.Panel(parent)
    bar.SetBackgroundColour(parent.GetBackgroundColour())
    sizer = wx.BoxSizer(wx.HORIZONTAL)
    rule = wx.Panel(bar, size=bar.FromDIP((4, -1)))
    rule.SetBackgroundColour(INK)
    sizer.Add(rule, 0, wx.EXPAND)
    bar.title = text(bar, label, size, weight="bold")
    sizer.Add(bar.title, 1, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, bar.FromDIP(10))
    bar.SetSizer(sizer)
    bar.SetMinSize(bar.FromDIP((-1, scaled(28))))
    return bar


def chip(parent, label, tone="idle", size=13):
    """Filled state label; recolour later with set_chip."""
    panel = wx.Panel(parent)
    sizer = wx.BoxSizer(wx.HORIZONTAL)
    panel.label = wx.StaticText(panel, label=label)
    panel.label.SetFont(font(size, "semibold"))
    sizer.Add(panel.label, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, panel.FromDIP(10))
    panel.SetSizer(sizer)
    panel.SetMinSize(panel.FromDIP((-1, scaled(32))))
    set_chip(panel, label, tone)
    return panel


def set_chip(panel, label, tone):
    fill, colour = TONES[tone]
    changed = panel.label.GetLabel() != label
    recoloured = False
    for window, getter, setter, value in (
        (panel, "GetBackgroundColour", "SetBackgroundColour", fill),
        (panel.label, "GetBackgroundColour", "SetBackgroundColour", fill),
        (panel.label, "GetForegroundColour", "SetForegroundColour", colour),
    ):
        if getattr(window, getter)() != wx.Colour(value):
            getattr(window, setter)(value)
            recoloured = True
    if changed:
        panel.label.SetLabel(label)
        panel.GetParent().Layout()
    if changed or recoloured:
        panel.Refresh(False)
