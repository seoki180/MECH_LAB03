"""Flat, full-surface buttons with shared mouse, keyboard and selection feedback."""
import wx
from wx.lib.buttons import GenButton
from experiment_app.ui.theme import BUTTON_COLOURS, SIGNAL, CONTROL_HEIGHT, font, scaled


class BoxButton(GenButton):
    def __init__(self, parent, label, primary=False, height=CONTROL_HEIGHT, icon=None, align_left=False, tone=None):
        self.tone = tone or ("primary" if primary else "sheet")
        self.icon = icon  # optional wx.BitmapBundle drawn above the label
        self.align_left = align_left
        self.selected = False
        self.primary = primary
        self.hovered = False
        self.flashing = False
        self.key_held = None
        super().__init__(parent, label=label, style=wx.BORDER_NONE)
        self.SetFont(font(14, "semibold" if primary else "regular"))
        # An explicit width overrides DoGetBestSize in wx sizers. Leave width
        # automatic so even short Korean labels receive their measured padding.
        self.SetMinSize(self.FromDIP((-1, height)))
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.SetCursor(wx.Cursor(wx.CURSOR_HAND))
        self.flash_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._end_flash, self.flash_timer)
        self.Bind(wx.EVT_ENTER_WINDOW, self._enter)
        self.Bind(wx.EVT_LEAVE_WINDOW, self._leave)
        self.Bind(wx.EVT_WINDOW_DESTROY, self._destroy)

    def SetSelected(self, selected):
        if self.selected != bool(selected):
            self.selected = bool(selected)
            self.Refresh(False)

    def DoGetBestSize(self):
        width, height = self.GetTextExtent(self.GetLabel())
        if self.icon:
            icon = self.icon.GetPreferredLogicalSizeFor(self)
            return wx.Size(max(self.FromDIP(scaled(88)), width + self.FromDIP(24)),
                           max(self.FromDIP(CONTROL_HEIGHT), icon.height + height + self.FromDIP(20)))
        return wx.Size(max(self.FromDIP(CONTROL_HEIGHT), width + self.FromDIP(32)),
                       max(self.FromDIP(CONTROL_HEIGHT), height + self.FromDIP(16)))

    def SetLabel(self, label):
        if self.GetLabel() == label:
            return
        super().SetLabel(label)
        self.InvalidateBestSize()
        self.Refresh(False)
        if hasattr(self, "flash_timer"):
            parent = self.GetParent()
            parent.InvalidateBestSize()
            parent.Layout()

    def SetTone(self, tone):
        if self.tone != tone:
            self.tone = tone
            self.Refresh(False)

    def colours(self):
        palette = BUTTON_COLOURS[self.tone]
        if not self.IsEnabled():
            return palette["disabled_selected" if self.selected else "disabled"]
        if not self.up or self.flashing:
            return palette["pressed"]
        if self.selected:
            return palette["selected"]
        if self.hovered or self.hasFocus:
            return palette["hover"]
        return palette["normal"]

    def OnPaint(self, event):
        dc = wx.AutoBufferedPaintDC(self)
        background, foreground, border = self.colours()
        dc.SetBackground(wx.Brush(background))
        dc.Clear()
        width, height = self.GetClientSize()
        if border:
            dc.SetPen(wx.Pen(border, self.FromDIP(1)))
            dc.SetBrush(wx.TRANSPARENT_BRUSH)
            dc.DrawRectangle(0, 0, width, height)
        if self.selected and self.align_left:
            # List rows mark the selection with a signal bar instead of a text prefix.
            dc.SetPen(wx.TRANSPARENT_PEN)
            dc.SetBrush(wx.Brush(SIGNAL))
            dc.DrawRectangle(0, 0, self.FromDIP(4), height)
        dc.SetFont(self.GetFont())
        dc.SetTextForeground(foreground)
        label = wx.Control.Ellipsize(self.GetLabel(), dc, wx.ELLIPSIZE_END,
                                    max(1, width - self.FromDIP(16)))
        tw, th = dc.GetTextExtent(label)
        if not self.icon:
            left = self.FromDIP(16) if self.align_left else (width - tw) // 2
            dc.DrawText(label, left, (height - th) // 2)
            return
        bitmap = self._tinted_icon(foreground)
        iw, ih = bitmap.GetLogicalSize()
        gap = self.FromDIP(4)
        top = (height - ih - gap - th) // 2
        dc.DrawBitmap(bitmap, (width - iw) // 2, top, True)
        dc.DrawText(label, (width - tw) // 2, top + ih + gap)

    def _tinted_icon(self, colour):
        """Recolour the monochrome SVG to the label colour so it stays legible on every state."""
        bitmap = self.icon.GetBitmapFor(self)
        image = bitmap.ConvertToImage()
        if not image.HasAlpha():
            image.InitAlpha()
        colour = wx.Colour(colour)
        image.SetRGB(wx.Rect(0, 0, image.GetWidth(), image.GetHeight()),
                     colour.Red(), colour.Green(), colour.Blue())
        tinted = image.ConvertToBitmap()
        tinted.SetScaleFactor(bitmap.GetScaleFactor())
        return tinted

    def Notify(self):
        if not self.IsEnabled():
            return
        self.flashing = True
        self.flash_timer.StartOnce(160)
        self.Refresh(False)
        super().Notify()  # The callback may destroy this control; don't access it afterwards.

    def OnLeftUp(self, event):
        if not self.HasCapture():
            return
        inside = self.GetClientRect().Contains(event.GetPosition())
        activate = self.IsEnabled() and not self.up and inside
        self.up = True
        self.ReleaseMouse()
        self.Refresh(False)
        if activate:
            self.Notify()

    def cancel_press(self):
        """끌기가 스크롤로 바뀔 때 눌림 표시만 되돌린다. 콜백은 실행하지 않는다."""
        self.key_held = None
        if not self.up:
            self.up = True
            self.Refresh(False)

    def OnKeyDown(self, event):
        key = event.GetKeyCode()
        if self.hasFocus and self.IsEnabled() and key in (wx.WXK_SPACE, wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            if self.key_held is None:
                self.key_held = key
                self.up = False
                self.Refresh(False)
            return
        event.Skip()

    def OnKeyUp(self, event):
        if self.key_held == event.GetKeyCode():
            self.key_held = None
            self.up = True
            self.Refresh(False)
            if self.hasFocus and self.IsEnabled():
                self.Notify()
            return
        event.Skip()

    def OnLoseFocus(self, event):
        self.key_held = None
        self.up = True
        super().OnLoseFocus(event)

    def Enable(self, enable=True):
        if not enable:
            self.up = True
            self.key_held = None
            self.flashing = False
            if self.HasCapture():
                self.ReleaseMouse()
        return super().Enable(enable)

    def _enter(self, event):
        self.hovered = True
        self.Refresh(False)
        event.Skip()

    def _leave(self, event):
        self.hovered = False
        self.Refresh(False)
        event.Skip()

    def _end_flash(self, event):
        self.flashing = False
        self.Refresh(False)

    def _destroy(self, event):
        if event.GetEventObject() is self:
            self.flash_timer.Stop()
        event.Skip()
