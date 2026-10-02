"""Windows-only vertical scrollbar painted in the existing HWND's nonclient area.

No child window, overlay, global metric change, or replacement content sizer.
Win32 is loaded lazily so geometry/message tests also run on macOS/Linux.
"""
import ctypes as C
from dataclasses import dataclass
import logging

log = logging.getLogger(__name__)
LONG, UINT, HANDLE = C.c_int32, C.c_uint32, C.c_void_p


class RECT(C.Structure):
    _fields_ = [(name, LONG) for name in ("left", "top", "right", "bottom")]


class POINT(C.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


class SCROLLINFO(C.Structure):
    _fields_ = [("cbSize", UINT), ("fMask", UINT), ("nMin", LONG),
                ("nMax", LONG), ("nPage", UINT), ("nPos", LONG), ("nTrackPos", LONG)]


@dataclass(frozen=True)
class ScrollState:
    minimum: int
    limit: int
    page: int
    position: int

    @property
    def maximum(self):
        return max(self.minimum, self.limit - max(0, self.page - 1))

    def clamp(self, value):
        return max(self.minimum, min(self.maximum, value))


@dataclass(frozen=True)
class BarGeometry:
    left: int
    top: int
    width: int
    height: int
    thumb_top: int
    thumb_height: int

    def contains(self, x, y):
        return self.left <= x < self.left + self.width and self.top <= y < self.top + self.height

    def position_for(self, y, grab_offset, state):
        travel = self.height - self.thumb_height
        fraction = max(0, min(1, (y - self.top - grab_offset) / max(1, travel)))
        return state.clamp(round(state.minimum + fraction * (state.maximum - state.minimum)))


def bar_geometry(left, top, width, height, state, minimum_thumb):
    height = max(0, height)
    length = min(height, max(minimum_thumb,
                            round(height * state.page / max(1, state.limit - state.minimum + 1))))
    travel = height - length
    offset = round(travel * (state.clamp(state.position) - state.minimum)
                   / max(1, state.maximum - state.minimum))
    return BarGeometry(left, top, width, height, top + offset, length)


def narrow_client(rect, desired_width, native_width):
    # Default processing already accounted for the original native scrollbar.
    # Only the right edge changes; never subtract anything from the bottom.
    rect.right = max(rect.left, rect.right - max(0, desired_width - native_width))


def signed_point(lparam):
    return C.c_int16(lparam & 0xffff).value, C.c_int16((lparam >> 16) & 0xffff).value


WM_NCDESTROY, WM_NCCALCSIZE, WM_NCHITTEST = 0x82, 0x83, 0x84
WM_NCPAINT, WM_NCACTIVATE = 0x85, 0x86
WM_NCMOUSEMOVE, WM_NCLBUTTONDOWN, WM_NCLBUTTONDBLCLK = 0xa0, 0xa1, 0xa3
WM_MOUSEMOVE, WM_LBUTTONUP, WM_CAPTURECHANGED = 0x200, 0x202, 0x215
WM_CANCELMODE, WM_PAINT, WM_SIZE = 0x1f, 0xf, 5
WM_DPICHANGED_AFTERPARENT, WM_THEMECHANGED = 0x2e3, 0x31a
HTVSCROLL = 7


class NonClientScrollbar:
    def __init__(self, hwnd, on_scroll, *, width_dip=48, thumb_dip=48, api=None):
        self.hwnd, self.on_scroll = hwnd, on_scroll
        self.api = api if api is not None else native_api()
        self.width_dip, self.thumb_dip = width_dip, thumb_dip
        self.grab = None
        self.active = True
        self._last = None
        self.api.install(self)
        try:
            self.api.reframe(hwnd)
        except Exception:
            self.dispose()
            raise

    def pixels(self, dip):
        return max(1, round(dip * self.api.dpi(self.hwnd) / 96))

    def geometry(self):
        if not self.active or not self.api.vertical(self.hwnd):
            return None
        window, client = self.api.rectangles(self.hwnd)
        return bar_geometry(client.right - window.left, client.top - window.top,
                            min(self.pixels(self.width_dip), window.right - client.right),
                            client.bottom - client.top, self.api.scroll_state(self.hwnd),
                            self.pixels(self.thumb_dip))

    def refresh(self, force=False):
        bar = self.geometry()
        signature = (bar, self.grab is not None)
        if bar is not None and (force or signature != self._last):
            self.api.paint(self.hwnd, bar, self.grab is not None)
        self._last = signature

    def _window_point(self, x, y):
        window, _ = self.api.rectangles(self.hwnd)
        return x - window.left, y - window.top

    def _scroll(self, position):
        self.on_scroll(self.api.scroll_state(self.hwnd).clamp(position))
        self.refresh(force=True)

    def cancel_drag(self):
        self.grab = None
        if self.api.has_capture(self.hwnd):
            self.api.release_capture()

    def handle(self, message, wparam, lparam, default):
        if message == WM_NCDESTROY:
            self.dispose(destroying=True)
            return default()
        if not self.active:
            return default()
        if message == WM_NCCALCSIZE:
            result = default()
            if self.api.vertical(self.hwnd) and lparam:
                # NCCALCSIZE_PARAMS starts with rgrc[0], also valid for wParam=FALSE.
                rect = C.cast(lparam, C.POINTER(RECT)).contents
                narrow_client(rect, self.pixels(self.width_dip), self.api.native_width(self.hwnd))
                return 0  # Do not reuse native WVR_VALIDRECTS after changing the client rect.
            return result
        if message in (WM_NCHITTEST, WM_NCLBUTTONDOWN, WM_NCLBUTTONDBLCLK, WM_NCMOUSEMOVE):
            bar = self.geometry()
            x, y = self._window_point(*signed_point(lparam))
            if bar and bar.contains(x, y):
                if message == WM_NCHITTEST:
                    return HTVSCROLL
                if message in (WM_NCLBUTTONDOWN, WM_NCLBUTTONDBLCLK):
                    state = self.api.scroll_state(self.hwnd)
                    if state.maximum > state.minimum:
                        if bar.thumb_top <= y < bar.thumb_top + bar.thumb_height:
                            self.grab = y - bar.thumb_top
                            self.api.set_capture(self.hwnd)
                        else:
                            direction = -1 if y < bar.thumb_top else 1
                            self._scroll(state.position + direction * max(1, state.page))
                    self.refresh(force=True)
                return 0  # Suppress native tracking/hover painting only on our strip.
        if message == WM_MOUSEMOVE and self.grab is not None:
            bar = self.geometry()
            if bar and self.api.has_capture(self.hwnd):
                point = self.api.client_to_screen(self.hwnd, *signed_point(lparam))
                _, y = self._window_point(*point)
                self._scroll(bar.position_for(y, self.grab, self.api.scroll_state(self.hwnd)))
            else:
                self.cancel_drag()
            return 0
        if message == WM_LBUTTONUP and self.grab is not None:
            self.cancel_drag()
            self.refresh(force=True)
            return 0
        if message in (WM_CANCELMODE, WM_CAPTURECHANGED):
            if message == WM_CANCELMODE:
                self.cancel_drag()
            else:
                self.grab = None
            self.refresh(force=True)
        result = default()
        if message in (WM_DPICHANGED_AFTERPARENT, WM_THEMECHANGED):
            self.api.reframe(self.hwnd)
        if message in (WM_NCPAINT, WM_NCACTIVATE, WM_DPICHANGED_AFTERPARENT, WM_THEMECHANGED):
            # OS가 방금 비클라이언트 영역을 지웠으니 변화 여부와 무관하게 다시 그린다.
            self.refresh(force=True)
        elif message in (WM_PAINT, WM_SIZE):
            # 클라이언트 영역 리페인트(센서 값 갱신 등)는 비클라이언트 띠와 무관하다.
            # geometry()가 이미 크기 변화를 반영하므로 바뀌었을 때만 다시 그려
            # 매 WM_PAINT마다 깜빡이지 않게 한다.
            self.refresh()
        return result

    def dispose(self, destroying=False):
        if not self.active:
            return
        self.active = False
        self.cancel_drag()
        self.api.remove(self)
        self.on_scroll = lambda position: None
        if not destroying:
            self.api.reframe(self.hwnd)


_native_api = None


def native_api():
    global _native_api
    if _native_api is None:
        _native_api = Win32API()
    return _native_api


class Win32API:
    """Typed pointer-sized ABI; all operations stay on the HWND's GUI thread."""
    def __init__(self):
        import sys
        if sys.platform != "win32":
            raise OSError("Nonclient scrollbars require Windows")
        self.user = C.WinDLL("user32", use_last_error=True)
        self.comctl = C.WinDLL("comctl32", use_last_error=True)
        self.gdi = C.WinDLL("gdi32", use_last_error=True)
        self.controllers = {}
        self.callback_type = C.WINFUNCTYPE(C.c_ssize_t, HANDLE, UINT, C.c_size_t,
                                          C.c_ssize_t, C.c_size_t, C.c_size_t)
        # One process-lifetime callback prevents ctypes callback GC after window close.
        self.callback = self.callback_type(self._dispatch)
        for library, name, result, arguments in (
            (self.comctl, "SetWindowSubclass", LONG, [HANDLE, self.callback_type, C.c_size_t, C.c_size_t]),
            (self.comctl, "RemoveWindowSubclass", LONG, [HANDLE, self.callback_type, C.c_size_t]),
            (self.comctl, "DefSubclassProc", C.c_ssize_t, [HANDLE, UINT, C.c_size_t, C.c_ssize_t]),
            (self.user, "GetWindowLongW", LONG, [HANDLE, LONG]),
            (self.user, "GetDpiForWindow", UINT, [HANDLE]),
            (self.user, "GetSystemMetricsForDpi", LONG, [LONG, UINT]),
            (self.user, "GetWindowRect", LONG, [HANDLE, C.POINTER(RECT)]),
            (self.user, "GetClientRect", LONG, [HANDLE, C.POINTER(RECT)]),
            (self.user, "ClientToScreen", LONG, [HANDLE, C.POINTER(POINT)]),
            (self.user, "GetScrollInfo", LONG, [HANDLE, LONG, C.POINTER(SCROLLINFO)]),
            (self.user, "SetWindowPos", LONG, [HANDLE, HANDLE, LONG, LONG, LONG, LONG, UINT]),
            (self.user, "GetWindowDC", HANDLE, [HANDLE]),
            (self.user, "ReleaseDC", LONG, [HANDLE, HANDLE]),
            (self.user, "FillRect", LONG, [HANDLE, C.POINTER(RECT), HANDLE]),
            (self.user, "GetSysColor", UINT, [LONG]),
            (self.user, "SetCapture", HANDLE, [HANDLE]),
            (self.user, "GetCapture", HANDLE, []),
            (self.user, "ReleaseCapture", LONG, []),
            (self.gdi, "CreateSolidBrush", HANDLE, [UINT]),
            (self.gdi, "DeleteObject", LONG, [HANDLE]),
        ):
            function = getattr(library, name)
            function.restype, function.argtypes = result, arguments

    @staticmethod
    def require(result):
        if not result:
            raise C.WinError(C.get_last_error())
        return result

    def install(self, controller):
        ident = id(controller)
        self.controllers[ident] = controller
        try:
            self.require(self.comctl.SetWindowSubclass(controller.hwnd, self.callback, ident, 0))
        except Exception:
            self.controllers.pop(ident, None)
            raise

    def remove(self, controller):
        self.comctl.RemoveWindowSubclass(controller.hwnd, self.callback, id(controller))
        self.controllers.pop(id(controller), None)

    def _dispatch(self, hwnd, message, wparam, lparam, ident, refdata):
        result = []
        def default():
            if not result:
                result.append(self.comctl.DefSubclassProc(hwnd, message, wparam, lparam))
            return result[0]
        controller = self.controllers.get(ident)
        if controller is None:
            return default()
        try:
            return controller.handle(message, wparam, lparam, default)
        except Exception:
            # Never allow a Python exception through a native callback. Restore the
            # standard frame instead of leaving an unusable blank nonclient strip.
            log.exception("Windows nonclient scrollbar failed; restoring native scrollbar")
            try:
                controller.dispose(destroying=message == WM_NCDESTROY)
            except Exception:
                log.exception("Could not detach nonclient scrollbar")
            return default()

    def reframe(self, hwnd):
        # SWP_NOSIZE | NOMOVE | NOZORDER | NOACTIVATE | FRAMECHANGED
        self.require(self.user.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x37))

    def vertical(self, hwnd):
        return bool(self.user.GetWindowLongW(hwnd, -16) & 0x00200000)  # WS_VSCROLL

    def dpi(self, hwnd):
        return self.user.GetDpiForWindow(hwnd) or 96

    def native_width(self, hwnd):
        return self.user.GetSystemMetricsForDpi(2, self.dpi(hwnd))  # SM_CXVSCROLL

    def client_to_screen(self, hwnd, x, y):
        point = POINT(x, y)
        self.require(self.user.ClientToScreen(hwnd, C.byref(point)))
        return point.x, point.y

    def rectangles(self, hwnd):
        window, client = RECT(), RECT()
        self.require(self.user.GetWindowRect(hwnd, C.byref(window)))
        self.require(self.user.GetClientRect(hwnd, C.byref(client)))
        left, top = self.client_to_screen(hwnd, client.left, client.top)
        right, bottom = self.client_to_screen(hwnd, client.right, client.bottom)
        return window, RECT(left, top, right, bottom)

    def scroll_state(self, hwnd):
        info = SCROLLINFO()
        info.cbSize, info.fMask = C.sizeof(info), 7  # SIF_RANGE | PAGE | POS
        self.require(self.user.GetScrollInfo(hwnd, 1, C.byref(info)))
        return ScrollState(info.nMin, info.nMax, info.nPage, info.nPos)

    def set_capture(self, hwnd):
        self.user.SetCapture(hwnd)

    def has_capture(self, hwnd):
        return self.user.GetCapture() == hwnd

    def release_capture(self):
        self.user.ReleaseCapture()

    def paint(self, hwnd, bar, dragging):
        dc = self.require(self.user.GetWindowDC(hwnd))
        try:
            # Restrict every fill to the nonclient strip; never clear the client DC.
            for rect, colour in (
                (RECT(bar.left, bar.top, bar.left + bar.width, bar.top + bar.height), 0),
                (RECT(bar.left, bar.thumb_top, bar.left + bar.width,
                      bar.thumb_top + bar.thumb_height), 13 if dragging else 16),
            ):
                brush = self.require(self.gdi.CreateSolidBrush(self.user.GetSysColor(colour)))
                try:
                    self.require(self.user.FillRect(dc, C.byref(rect), brush))
                finally:
                    self.gdi.DeleteObject(brush)
        finally:
            self.user.ReleaseDC(hwnd, dc)
