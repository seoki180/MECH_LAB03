"""Win32 message/geometry checks; fake OS boundary, no claim of native paint coverage."""
import ctypes
import pytest
from experiment_app.ui.adapters.nonclient_scrollbar import (
    RECT, ScrollState, bar_geometry, narrow_client, signed_point,
)


@pytest.mark.parametrize("scale,native,width", [(1, 17, 48), (1.5, 26, 72), (2, 34, 96)])
def test_only_right_edge_changes_for_dpi(scale, native, width):
    rect = RECT(10, 20, 1010 - native, 788)
    narrow_client(rect, width, native)
    assert (rect.left, rect.top, rect.right, rect.bottom) == (10, 20, 1010 - width, 788)


def test_tiny_client_never_gets_negative_width():
    rect = RECT(0, 0, 12, 100)
    narrow_client(rect, 48, 17)
    assert rect.right == rect.left and rect.bottom == 100


def test_thumb_position_uses_full_32_bit_range():
    state = ScrollState(0, 200000, 800, 199201)
    bar = bar_geometry(100, 10, 48, 600, state, 48)
    assert bar.thumb_height == 48
    assert bar.thumb_top == 562
    assert bar.position_for(562, 0, state) == state.maximum == 199201
    assert bar.position_for(-100, 0, state) == 0
    assert bar.position_for(10000, 0, state) == 199201


def test_empty_range_and_short_track():
    state = ScrollState(0, 99, 100, 0)
    bar = bar_geometry(100, 10, 48, 20, state, 48)
    assert state.maximum == 0
    assert bar.thumb_height == 20
    assert bar.position_for(15, 5, state) == 0


def test_nonzero_minimum_and_page_zero():
    state = ScrollState(100, 1000, 0, 500)
    bar = bar_geometry(0, 0, 48, 400, state, 48)
    assert state.maximum == 1000
    assert bar.position_for(400, 0, state) == 1000
    assert bar.position_for(-100, 0, state) == 100


def test_negative_monitor_coordinates_are_signed():
    packed = ((-200 & 0xffff) << 16) | (-1200 & 0xffff)
    assert signed_point(packed) == (-1200, -200)


def test_rect_abi_uses_windows_long_even_on_non_windows_hosts():
    assert ctypes.sizeof(RECT) == 16


class FakeAPI:
    def __init__(self):
        self.visible = True
        self.state = ScrollState(0, 200000, 800, 0)
        self.capture = False
        self.paints = []
        self.removed = False
        self.frames = 0

    def install(self, controller):
        self.controller = controller

    def remove(self, controller):
        self.removed = True

    def reframe(self, hwnd):
        self.frames += 1

    def vertical(self, hwnd):
        return self.visible

    def dpi(self, hwnd):
        return 96

    def native_width(self, hwnd):
        return 17

    def scroll_state(self, hwnd):
        return self.state

    def rectangles(self, hwnd):
        return RECT(-100, 0, 500, 600), RECT(-100, 0, 452, 600)

    def client_to_screen(self, hwnd, x, y):
        return x - 100, y

    def paint(self, hwnd, geometry, dragging):
        self.paints.append(geometry)

    def set_capture(self, hwnd):
        self.capture = True

    def has_capture(self, hwnd):
        return self.capture

    def release_capture(self):
        self.capture = False


def make_controller():
    from experiment_app.ui.adapters.nonclient_scrollbar import NonClientScrollbar
    api = FakeAPI()
    positions = []
    def scroll(position):
        positions.append(position)
        api.state = ScrollState(0, 200000, 800, position)
    controller = NonClientScrollbar(123, scroll, api=api)
    return controller, api, positions


def packed(x, y):
    return ((y & 0xffff) << 16) | (x & 0xffff)


@pytest.mark.parametrize("valid_rects", [0, 1])
def test_nc_calc_calls_default_once_and_keeps_bottom(valid_rects):
    controller, api, _ = make_controller()
    # NCCALCSIZE_PARAMS begins with the same RECT in both message variants.
    rects = (RECT * 3)(RECT(0, 0, 600, 500), RECT(), RECT())
    calls = []
    def default():
        calls.append(True)
        rects[0].right -= 17
        return 0x400
    assert controller.handle(0x83, valid_rects, ctypes.addressof(rects), default) == 0
    assert len(calls) == 1
    assert rects[0].right == 552 and rects[0].bottom == 500


def test_nc_hit_drag_and_release_preserve_full_range():
    controller, api, positions = make_controller()
    default = lambda: -999
    assert controller.handle(0x84, 0, packed(470, 20), default) == 7
    assert controller.handle(0x84, 0, packed(400, 20), default) == -999
    controller.handle(0xa1, 7, packed(470, 10), default)
    assert api.capture
    controller.handle(0x200, 1, packed(570, 900), default)
    assert positions[-1] == 199201
    controller.handle(0x202, 0, 0, default)
    assert not api.capture and controller.grab is None


def test_track_click_pages_and_capture_loss_cancels():
    controller, api, positions = make_controller()
    controller.handle(0xa1, 7, packed(470, 500), lambda: 0)
    assert positions == [800] and not api.capture
    controller.handle(0xa1, 7, packed(470, 10), lambda: 0)
    controller.handle(0x215, 0, 0, lambda: 0)
    assert controller.grab is None


def test_hidden_native_bar_does_not_reserve_space_or_paint():
    controller, api, _ = make_controller()
    api.visible = False
    rect = RECT(0, 0, 600, 500)
    controller.handle(0x83, 0, ctypes.addressof(rect), lambda: 0)
    controller.refresh()
    assert rect.right == 600 and rect.bottom == 500
    assert not api.paints


def test_destroy_removes_subclass_and_releases_capture():
    controller, api, _ = make_controller()
    api.capture = True
    assert controller.handle(0x82, 0, 0, lambda: 99) == 99
    assert api.removed and not api.capture and not controller.active
    previous = len(api.paints)
    controller.refresh()
    assert len(api.paints) == previous


def test_refresh_only_paints_changed_geometry_or_state():
    controller, api, _ = make_controller()
    controller.refresh()
    count = len(api.paints)
    controller.refresh()
    assert len(api.paints) == count
    api.state = ScrollState(0, 200000, 800, 100000)
    controller.refresh()
    assert len(api.paints) == count + 1
