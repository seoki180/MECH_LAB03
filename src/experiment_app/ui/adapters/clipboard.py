import wx


class WxClipboard:
    def set_text(self, text):
        if not wx.TheClipboard.Open():
            return False
        try:
            result = wx.TheClipboard.SetData(wx.TextDataObject(text))
            wx.TheClipboard.Flush()
            return result
        finally:
            wx.TheClipboard.Close()
