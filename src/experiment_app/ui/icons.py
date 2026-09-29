"""Optional command icons from asset/icons/<key>.svg or <key>.png; missing files fall back to text-only buttons."""
from pathlib import Path
import wx

ICON_DIR = Path(__file__).resolve().parents[3] / "asset" / "icons"
ICON_SIZE = 18  # DIP
SCALES = (1, 1.5, 2)


def load(key):
    svg = ICON_DIR / f"{key}.svg"
    if svg.is_file():
        bundle = wx.BitmapBundle.FromSVGFile(str(svg), wx.Size(ICON_SIZE, ICON_SIZE))
        return bundle if bundle.IsOk() else None
    png = ICON_DIR / f"{key}.png"
    if not png.is_file():
        return None
    image = wx.Image(str(png), wx.BITMAP_TYPE_PNG)
    if not image.IsOk():
        return None
    sizes = [round(ICON_SIZE * scale) for scale in SCALES]
    return wx.BitmapBundle.FromBitmaps([wx.Bitmap(image.Scale(s, s, wx.IMAGE_QUALITY_HIGH)) for s in sizes])


def logo(filename, height):
    """Brand image from asset/icons scaled to height DIP with its aspect ratio kept; None if missing."""
    png = ICON_DIR / filename
    if not png.is_file():
        return None
    image = wx.Image(str(png), wx.BITMAP_TYPE_PNG)
    if not image.IsOk():
        return None
    ratio = image.GetWidth() / image.GetHeight()
    return wx.BitmapBundle.FromBitmaps([
        wx.Bitmap(image.Scale(round(height * s * ratio), round(height * s), wx.IMAGE_QUALITY_HIGH)) for s in SCALES])
