"""Opt-in native GUI smoke test. Run from the project root on a desktop session."""
import io
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time
import traceback
import wx
from experiment_app.bootstrap import build_services
from experiment_app.demo.fixtures import BASIC_FIELDS, SCENARIOS
from sample_tests import seed
from experiment_app.ui.frames.main_frame import MainFrame
from experiment_app.ui.adapters.clipboard import WxClipboard
from experiment_app.infrastructure.tiles import MapPackSet
from experiment_app.ui.adapters.tile_map import MIN_ZOOM, MAX_ZOOM
from experiment_app.ui.adapters.mercator import lonlat_to_pixel
from experiment_app.domain.session import State
from experiment_app.domain.test_definition import AppError, FieldPatch

OUTPUT = Path("artifacts")
OUTPUT.mkdir(exist_ok=True)
EDITED_NAME = f"GUI smoke 저장 {time.time_ns()}"
DEMO_BOUNDS = (127.09, 37.39, 127.11, 37.41)
report = {"wx_version": wx.version(), "checks": [], "errors": [],
          "visual_verification": "미완료 · 현재 환경에서 화면 캡처가 검게 반환됨"}
app = wx.App(False)


def build_demo_pack(directory, min_zoom=10, max_zoom=16):
    """Synthetic MBTiles pack so the offline map renderer runs without a downloaded pack."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "smoke.mbtiles"
    path.unlink(missing_ok=True)
    image = wx.Image(256, 256)
    image.SetRGB(wx.Rect(0, 0, 256, 256), 96, 124, 92)
    stream = io.BytesIO()
    image.SaveFile(stream, wx.BITMAP_TYPE_JPEG)
    data = stream.getvalue()
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE metadata (name text, value text);
        CREATE TABLE tiles (zoom_level integer, tile_column integer, tile_row integer, tile_data blob);
    """)
    connection.executemany("INSERT INTO metadata VALUES (?, ?)", [
        ("name", "smoke"), ("format", "jpg"), ("minzoom", str(min_zoom)), ("maxzoom", str(max_zoom)),
        ("bounds", ",".join(str(value) for value in DEMO_BOUNDS)), ("attribution", "GUI smoke synthetic tiles")])
    for zoom in range(min_zoom, max_zoom + 1):
        left, top = lonlat_to_pixel(DEMO_BOUNDS[0], DEMO_BOUNDS[3], zoom)
        right, bottom = lonlat_to_pixel(DEMO_BOUNDS[2], DEMO_BOUNDS[1], zoom)
        for x in range(int(left // 256), int(right // 256) + 1):
            for y in range(int(top // 256), int(bottom // 256) + 1):
                connection.execute("INSERT INTO tiles VALUES (?, ?, ?, ?)",
                                   (zoom, x, (1 << zoom) - 1 - y, data))
    connection.commit()
    connection.close()
    return directory


# 이전 실행이 남긴 시험 폴더와 기록을 지우고 빈 상태에서 시작한다.
shutil.rmtree(OUTPUT / "smoke-data", ignore_errors=True)
shutil.rmtree(OUTPUT / "smoke-export", ignore_errors=True)
# 상한 없이 실제 운용과 같게 띄운다. 수집은 스모크가 중지 버튼을 누를 때 끝난다.
main, experiment = build_services(OUTPUT / "smoke-data")
tiles, pack_failures = MapPackSet.load(build_demo_pack(OUTPUT / "smoke-maps"))
frame = MainFrame(main, experiment, WxClipboard(), tiles, BASIC_FIELDS, SCENARIOS)
frame.Show()
frame.Raise()


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    report["checks"].append(message)


def capture(window, name):
    # Record native geometry; screenshots were blank on the execution host.
    report.setdefault("client_sizes", {})[name] = list(window.ToDIP(window.GetClientSize()))


def spin_wheel(view, notches, position=(10, 10)):
    event = wx.MouseEvent(wx.wxEVT_MOUSEWHEEL)
    event.SetWheelAxis(wx.MOUSE_WHEEL_VERTICAL)
    event.SetWheelDelta(120)
    event.SetWheelRotation(120 * notches)
    event.SetPosition(wx.Point(*position))
    view.GetEventHandler().ProcessEvent(event)


def pan_gesture(view, dy, position=(10, 10), start=True, end=False):
    event = wx.PanGestureEvent(view.GetId())
    event.SetEventObject(view)
    event.SetGestureStart(start)
    event.SetGestureEnd(end)
    event.SetDelta(wx.Point(0, dy))
    event.SetPosition(wx.Point(*position))
    view.GetEventHandler().ProcessEvent(event)


def finger_drag(view, dy, position=(10, 10)):
    """손가락으로 컨트롤을 눌러 끌 때 들어오는 마우스 흉내 입력."""
    down = wx.MouseEvent(wx.wxEVT_LEFT_DOWN)
    down.SetPosition(wx.Point(*position))
    view.GetEventHandler().ProcessEvent(down)
    motion = wx.MouseEvent(wx.wxEVT_MOTION)
    motion.SetLeftDown(True)
    motion.SetPosition(wx.Point(position[0], position[1] + dy))
    view.GetEventHandler().ProcessEvent(motion)
    up = wx.MouseEvent(wx.wxEVT_LEFT_UP)
    up.SetPosition(wx.Point(position[0], position[1] + dy))
    view.GetEventHandler().ProcessEvent(up)


def pinch(view, factor, position=(10, 10), start=True, end=True):
    event = wx.ZoomGestureEvent()
    event.SetEventObject(view)
    event.SetGestureStart(start)
    event.SetGestureEnd(end)
    event.SetZoomFactor(factor)
    event.SetPosition(wx.Point(*position))
    view.GetEventHandler().ProcessEvent(event)


def build_vector_pack(directory):
    """tilemaker 계열이 만드는 벡터 팩. 앱이 디코딩을 시도하면 안 된다."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "vector.mbtiles"
    path.unlink(missing_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE metadata (name text, value text);
        CREATE TABLE tiles (zoom_level integer, tile_column integer, tile_row integer, tile_data blob);
    """)
    connection.executemany("INSERT INTO metadata VALUES (?, ?)", [
        ("name", "vector smoke"), ("format", "pbf"), ("minzoom", "0"), ("maxzoom", "14")])
    connection.execute("INSERT INTO tiles VALUES (14, 1, 1, ?)", (b"\x1f\x8b\x08not an image",))
    connection.commit()
    connection.close()
    return directory


def check_map():
    pane = frame.experiment.map
    view = pane.map
    check(not pack_failures and view.tiles.available, "Offline tile pack loads from directory")
    view.Refresh()
    view.Update()
    check(any(view._bitmaps.values()), "Map pane blits offline tiles behind the GPS track")
    view.zoom_by(9)
    check(view.zoom == MAX_ZOOM, "Zoom in stops at the usable maximum instead of overzooming")
    view.zoom_by(-99)
    check(view.zoom == MIN_ZOOM, "Zoom out clamps to the usable minimum")

    base = view.zoom
    spin_wheel(view, 1)
    check(base < view.zoom < base + 1, "Mouse wheel zooms in by a fractional step")
    spin_wheel(view, -99)
    check(view.zoom == MIN_ZOOM, "Wheel zoom out clamps to the usable minimum")

    base = view.zoom
    pinch(view, 2.0)
    check(view.zoom == base + 1, "Pinch factor 2.0 raises zoom by exactly one level")

    snapshot = experiment.telemetry.snapshot()
    pane.render(snapshot)
    check(view.follow and view.center == snapshot.track[-1], "Map initially follows the latest GPS position")
    center = view.center
    view.zoom_to(view.zoom + 1, (10, 10))
    check(view.center == center, "Zoom preserves the tracked position")
    down = wx.MouseEvent(wx.wxEVT_LEFT_DOWN)
    down.SetPosition(wx.Point(10, 10))
    view.GetEventHandler().ProcessEvent(down)
    check(not view.follow and view.drag is not None and view.HasCapture(),
          "Dragging the map suspends automatic following")
    motion = wx.MouseEvent(wx.wxEVT_MOTION)
    motion.SetLeftDown(True)
    motion.SetPosition(wx.Point(80, 50))
    view.GetEventHandler().ProcessEvent(motion)
    check(view.center != center, "Dragging moves the map centre")
    view.up(wx.MouseEvent(wx.wxEVT_LEFT_UP))
    moved = view.center
    pane.render(snapshot)
    check(view.center == moved, "GPS updates preserve the manually moved map")
    pane.recenter_button.Notify()
    check(view.follow and view.center == center, "Current position button recentres and resumes tracking")
    check(all(list(control.ToDIP(control.GetSize())) == [48, 48] for control in pane.zoom_buttons),
          "Zoom buttons remain compact 48 DIP squares")
    pane.render(snapshot, live=False)
    check(view.center == center and not view.valid, "Ended session retains last position without showing a live fix")

    vector, vector_failures = MapPackSet.load(build_vector_pack(OUTPUT / "smoke-vector"))
    check(not vector.available and len(vector_failures) == 1 and "pbf" in str(vector_failures[0][1]),
          "Vector tile pack is refused at load with a reason instead of being decoded")


def check_experiment_touch_scroll(exp):
    """실험 본문도 손가락으로 스크롤되는지. 그림판만 자기 제스처를 쓴다."""
    body = exp.body
    unbound = []

    def walk(window, path="body"):
        for child in window.GetChildren():
            name = f"{path}/{type(child).__name__}"
            if getattr(child, "_wheel_scroll_owner", None) is not body:
                unbound.append(name)
            walk(child, name)

    walk(body)
    check(sorted(unbound) == ["body/GpsMapPane/Panel/TileMapView",
                              "body/ResultChartsPane/ChartCard/ChartView",
                              "body/ResultChartsPane/ChartCard/ChartView"],
          "Only the map and chart canvases keep their own gestures in the experiment body")
    check(body.GetVirtualSize().height > body.GetClientSize().height,
          "Experiment body has overflowing content to scroll")

    tile = exp.sensors["B"].cards["B.0"]
    for name, target in (("the body", body), ("a sensor pane", exp.sensors["B"]),
                         ("a metric card", tile), ("the map pane", exp.map),
                         ("a map header button", exp.map.zoom_buttons[0])):
        body.Scroll(0, 0)
        pan_gesture(target, -60)
        check(body.GetViewStart()[1] > 0, f"Touch pan over {name} scrolls the experiment body")
    for name, target in (("a metric value label", tile.value),
                         ("the map coordinate label", exp.map.coordinates),
                         ("a map header button", exp.map.zoom_buttons[0])):
        body.Scroll(0, 0)
        finger_drag(target, -60)
        check(body.GetViewStart()[1] > 0, f"Finger drag over {name} scrolls the experiment body")
    body.Scroll(0, 0)
    before = exp.map.map.center
    check(exp.map.map.center == before, "Dragging outside the map canvas does not move the map")


def check_chart_touch_scroll(exp):
    """결과 그래프 화면. 확대 전 그림판 끌기는 본문 스크롤로 넘어간다."""
    body = exp.body
    card = exp.charts.speed
    # 그래프 둘만 남으면 1024 DIP 창에는 다 들어가 스크롤할 것이 없다. 본문이 넘치는
    # 좁은 창으로 줄여 실제 스크롤 경로를 지나간다.
    restore = exp.GetClientSize()
    exp.SetClientSize(exp.FromDIP((700, 560)))
    exp.Layout()
    body.FitInside()
    wx.Yield()
    check(body.GetVirtualSize().height > body.GetClientSize().height,
          "Chart body overflows once the window is narrowed")
    for name, target in (("a chart card", card), ("the reset button", card.reset_button)):
        body.Scroll(0, 0)
        pan_gesture(target, -60)
        check(body.GetViewStart()[1] > 0, f"Touch pan over {name} scrolls the chart body")
    body.Scroll(0, 0)
    finger_drag(card.axis, -60)
    check(body.GetViewStart()[1] > 0, "Finger drag over the chart axis label scrolls the chart body")

    full = card.chart.bounds()
    body.Scroll(0, 0)
    finger_drag(card.chart, -60)
    check(body.GetViewStart()[1] > 0 and card.chart.bounds() == full,
          "Drag on an unzoomed chart scrolls the body instead of being swallowed")
    # Zoom at the plot centre: a fixed y=60 can hit the lower edge after layout
    # changes, where an upward drag correctly clamps instead of moving the chart.
    width, height = card.chart.GetClientSize()
    centre = wx.Point((width + card.chart.FromDIP(42)) // 2,
                      (height - card.chart.FromDIP(24)) // 2)
    card.chart.zoom_at(4.0, centre)
    zoomed = card.chart.bounds()
    body.Scroll(0, 0)
    finger_drag(card.chart, -60)
    check(body.GetViewStart()[1] == 0 and card.chart.bounds() != zoomed,
          "Drag on a zoomed chart pans the chart and leaves the body still")
    card.chart.reset_zoom()
    body.Scroll(0, 0)
    exp.SetClientSize(restore)
    exp.Layout()
    body.FitInside()
    wx.Yield()


def check_empty_start():
    """시험이 하나도 없는 첫 실행. 빈 화면에서도 다음 조치를 알 수 있어야 한다."""
    details = frame.tests.details
    check(not main.service.repository.list() and not main.service.repository.groups(),
          "First run starts with no demo tests and no groups")
    check(main.definition is None, "No test is selected when the store is empty")
    check(details.heading.GetLabel() == "시험이 없습니다" and details.summary.IsShown(),
          "Empty store explains how to add or import a test")
    check(details.fact_values["created"].GetLabel() == "—"
          or not details.fact_values["created"].IsShown(),
          "No creation date is shown without a test")
    check(not details.basic.IsShown() and not details.spec.IsShown(),
          "No editor fields are shown without a test")
    # 가져오기는 빈 상태에서도 눌려야 기존 시험 폴더를 들여올 수 있다.
    check(details.file_buttons["import"].IsEnabled(), "Import stays available on an empty store")
    check(not details.file_buttons["export"].IsShown(), "Export is hidden without a test")
    for key in ("edit", "duplicate", "open", "save"):
        check(not frame.header.buttons[key].IsEnabled(),
              f"Header '{key}' is disabled while the store is empty")
    check(frame.header.buttons["add"].IsEnabled(), "Add stays enabled on an empty store")


def check_new_test_has_no_prefilled_values():
    """새 시험에는 초기값이 없다. 입력란이 전부 비어 있고 advanced도 미설정이다."""
    group = main.service.repository.add_group("빈 값 확인 · 데모")
    main.new(group)
    draft = main.definition
    frame.render()
    details = frame.tests.details
    check(details.basic.controls["name"].GetValue() == "새 시험",
          "A new test only carries the placeholder name")
    values = draft.fields()
    unset = [p for p in values if p.startswith(("data/", "robot/"))]
    check(unset and all(values[p] is None for p in unset),
          f"Every value field of a new test is unset ({len(unset)} fields)")
    check(draft.ar_trapezoidal_step is None and draft.pf_straight_line is None,
          "Advanced robot sections stay absent on a new test")
    # 화면도 비어 있어야 한다. 0이나 꾸며낸 값이 보이면 안 된다.
    if not details.spec.advanced_shown():
        details.toggle_advanced()
    filled = []
    for editor, _, _ in details.spec.editors:
        for path, control in editor.controls.items():
            if isinstance(control, wx.TextCtrl) and control.GetValue() != "":
                filled.append((path, control.GetValue()))
            elif isinstance(control, wx.Choice) and control.GetStringSelection() != "미설정":
                filled.append((path, control.GetStringSelection()))
            elif isinstance(control, wx.CheckBox) \
                    and control.Get3StateValue() != wx.CHK_UNDETERMINED:
                filled.append((path, control.Get3StateValue()))
    check(not filled, f"No input shows a prefilled value on a new test (found {filled[:3]})")
    boxes = [c for editor, _, _ in details.spec.editors for c in editor.controls.values()
             if isinstance(c, wx.CheckBox)]
    check(boxes and all(b.Is3State() for b in boxes),
          "Robot option checkboxes are 3-state so unset differs from off")
    details.toggle_advanced()
    # 빈 값 그대로 저장된다. 검증은 입력한 값에만 적용된다.
    saved = main.service.save_new(draft, {})
    check(saved.revision == 1, "A new test saves with no value entered")
    check(all(v is None for p, v in saved.fields().items()
              if p.startswith(("data/", "robot/"))),
          "Saving an untouched new test does not invent values")
    main.select(saved.id)
    frame.render()
    check(frame.tests.details.basic.controls["name"].GetValue() == "새 시험",
          "The saved empty test reloads without invented values")
    # 생성일자는 저장된 뒤 화면에 보인다. 저장 전에는 꾸미지 않는다.
    check(details.fact_values["created"].GetLabel() != "—"
          and saved.created_utc.endswith("+00:00"),
          "Saved test shows its creation date; stored as timezone-aware UTC")
    # 잘못된 값은 여전히 거부된다.
    rejected = None
    try:
        main.service.save_patch(FieldPatch(saved.id, saved.revision,
                                           {"data/zero_brake_angle": "bad"}))
    except AppError as error:
        rejected = error
    check(rejected is not None and rejected.code == "VALIDATION_FAILED"
          and "data/zero_brake_angle" in rejected.errors,
          "The validator still rejects a non-numeric value on an empty test")
    main.service.repository.delete(saved.id)
    main.service.repository.delete_group(group)
    main.select(None)
    frame.render()


def later(action, delay=250):
    def guarded():
        try:
            action()
        except BaseException:
            report["errors"].append(traceback.format_exc())
            finish()
    wx.CallLater(delay, guarded)


def start():
    # 첫 실행은 빈 상태다. 데모 시험이 없는 화면부터 확인한 뒤 자료를 넣는다.
    check_empty_start()
    check_new_test_has_no_prefilled_values()
    seed(main.service.repository)
    main.select(main.service.repository.list()[0].id)
    frame.selected_group = main.definition.group_id
    frame.render()
    check(frame.tests.details.IsShown(), "Tests detail visible")
    capture(frame, "main-1280")
    details = frame.tests.details
    check(list(details.GetParent().GetChildren()) == [details],
          "Test details uses its own native scrollbar without a sibling overlay/control")
    check(details.GetVirtualSize().height > details.GetClientSize().height,
          "Test details has overflowing content")
    details.Scroll(0, details.GetVirtualSize().height)
    wx.Yield()
    check(details.GetScreenRect().Contains(details.advanced_toggle.GetScreenRect()),
          "The bottom control is fully reachable at the end of the details scroll range")
    details.Scroll(0, 0)
    spin_wheel(details.heading, -1)
    check(details.GetViewStart()[1] > 0,
          "First wheel over a child scrolls test details without dragging the scrollbar")
    details.Scroll(0, 0)
    pan_gesture(details.advanced_toggle, -60)
    check(details.GetViewStart()[1] > 0,
          "Touch pan gesture over a child scrolls test details")
    details.Scroll(0, 0)
    pan_gesture(details, -60)
    check(details.GetViewStart()[1] > 0, "Touch pan gesture over the panel itself scrolls it")
    details.Scroll(0, 0)
    finger_drag(details.heading, -60)
    check(details.GetViewStart()[1] > 0,
          "Finger drag over a label that cannot take gestures still scrolls")
    details.Scroll(0, 0)
    expanded_before = details.expanded
    finger_drag(details.advanced_toggle, -60)
    check(details.GetViewStart()[1] > 0 and details.expanded == expanded_before,
          "Finger drag on a button scrolls instead of activating it")
    check(details.advanced_toggle.up and not details.advanced_toggle.HasCapture(),
          "Button press state is cancelled when the drag becomes a scroll")
    details.Scroll(0, 0)
    tapped = details.expanded
    details.advanced_toggle.Notify()
    check(details.expanded != tapped, "A tap without dragging still activates the button")
    details.advanced_toggle.Notify()
    details.Scroll(0, 0)
    header = frame.header
    check(not header.title.IsShown() and not header.navigation, "Main navigation bar is removed")
    edit_button = header.buttons["edit"]
    down = wx.MouseEvent(wx.wxEVT_LEFT_DOWN)
    down.SetPosition(wx.Point(1, 1))
    edit_button.GetEventHandler().ProcessEvent(down)
    check(not edit_button.up, "Box corner press highlights entire button")
    up = wx.MouseEvent(wx.wxEVT_LEFT_UP)
    up.SetPosition(wx.Point(1, 1))
    edit_button.GetEventHandler().ProcessEvent(up)
    check(main.editing, "Box corner click opens edit mode")
    frame.cancel_edit()
    edit_button.Enable(False)
    edit_button.Notify()
    check(not edit_button.flashing, "Disabled button does not activate or flash")
    edit_button.Enable(True)
    edit_button.SetFocus()
    edit_button.OnGainFocus(wx.FocusEvent(wx.wxEVT_SET_FOCUS))
    key_down = wx.KeyEvent(wx.wxEVT_KEY_DOWN)
    key_down.SetKeyCode(wx.WXK_RETURN)
    edit_button.GetEventHandler().ProcessEvent(key_down)
    key_up = wx.KeyEvent(wx.wxEVT_KEY_UP)
    key_up.SetKeyCode(wx.WXK_RETURN)
    edit_button.GetEventHandler().ProcessEvent(key_up)
    check(main.editing, "Focused box button activates with Enter")
    frame.cancel_edit()
    check(not frame.tests.details.basic.controls["name"].IsEditable(), "Selection starts read-only")
    frame.begin_edit()
    frame.tests.details.basic.controls["name"].SetValue("cancel this draft")
    frame.cancel_edit()
    check(not main.editing and not main.changes
          and not frame.tests.details.basic.controls["name"].IsEditable(),
          "Cancel discards draft and locks native controls")
    frame.begin_edit()
    data_controls = {path: control for editor, _, _ in frame.tests.details.spec.editors
                     for path, control in editor.controls.items()}
    check(len(data_controls) == 35 and "data/zero_brake_angle" in data_controls,
          "Every experiment input field has a native editor")
    data_controls["robot/ar_trapezoidal_step/control"].SetValue("Position")
    data_controls["robot/ar_trapezoidal_step/apply_rate"].SetValue("526.32")
    data_controls["robot/pf_straight_line/control"].SetValue("Robot steering")
    data_controls["robot/pf_straight_line/start_x"].SetValue("-1000")
    choice = data_controls["robot/pf_straight_line/join_anywhere"]
    choice.SetValue(True)
    choice.GetEventHandler().ProcessEvent(wx.CommandEvent(wx.wxEVT_CHECKBOX, choice.GetId()))
    check({**main.definition.fields(), **main.changes}["robot/pf_straight_line/join_anywhere"] is True,
          "Robot checkbox creates a boolean draft")
    data_controls["data/zero_brake_angle"].SetValue("-15")
    check(main.changes["data/zero_brake_angle"] == "-15", "Experiment input creates a draft")
    frame.tests.details.basic.controls["name"].SetValue(EDITED_NAME)
    check(main.dirty, "Typing creates dirty draft")
    def inspect_settings():
        dialog = frame.settings_dialog
        try:
            check(dialog is not None and dialog.devices.scenario.GetStringSelection() == experiment.sessions.scenario,
                  "Settings button opens device settings with current scenario")
            dialog.devices.scenario.SetStringSelection("센서 지연/단절")
            dialog.devices.scenario.GetEventHandler().ProcessEvent(
                wx.CommandEvent(wx.wxEVT_CHOICE, dialog.devices.scenario.GetId()))
        finally:
            dialog.EndModal(wx.ID_CLOSE)
    later(inspect_settings, 100)
    header.buttons["settings"].Notify()
    def reopened_settings():
        dialog = frame.settings_dialog
        try:
            check(dialog.devices.scenario.GetStringSelection() == "센서 지연/단절",
                  "Settings retains device scenario across reopening")
            frame.set_scenario("정상")
        finally:
            dialog.EndModal(wx.ID_CLOSE)
    later(reopened_settings, 100)
    frame.open_settings()
    check(frame.settings_dialog is None and header is frame.header and main.changes["name"] == EDITED_NAME,
          "Closing settings preserves edit mode and draft")
    frame.save()
    later(saved)


def saved():
    if main.busy:
        later(saved)
        return
    check(not main.editing, "Successful save returns to read-only")
    check(main.definition.ar_trapezoidal_step["apply_rate"] == 526.32
          and main.definition.pf_straight_line["start_x"] == -1000
          and main.definition.pf_straight_line["join_anywhere"] is True,
          "Both robot settings save through native editors")
    frame.begin_edit()
    robot_editor = frame.tests.details.spec.editors[-1][0]
    robot_editor.controls["robot/pf_straight_line/join_anywhere"].SetValue(False)
    frame.cancel_edit()
    check(not main.dirty and main.definition.name == EDITED_NAME
          and main.definition.experiment_data["zero_brake_angle"] == -15,
          "Native editor saves experiment inputs asynchronously")
    frame.SetClientSize(frame.FromDIP((1024, 768)))
    later(narrow)


def narrow():
    check(frame.tests.narrow and not frame.tests.tree_card.IsShown(), "1024 DIP switches list to toggle")
    capture(frame, "main-1024")
    frame.tests.toggle_list()
    check(frame.tests.tree_card.IsShown(), "List remains reachable at 1024 DIP")
    frame.tests.selection_done()
    check_scenario()
    frame._prepare()
    later(prepared)


def check_scenario():
    """시험시나리오 pane과 내보내기/가져오기가 파일을 실제로 다루는지."""
    pane = frame.tests.details.scenario
    files = frame.tests.details.file_buttons
    check(pane.IsShown(), "Scenario pane is visible with the selected test")
    # 시험 파일 명령은 시험 이름 옆에, 시나리오 CSV 명령은 시나리오 구역에 있다.
    check(files["import"].GetParent() is frame.tests.details
          and files["export"].GetParent() is frame.tests.details,
          "Test import/export sit next to the test name, not in the header")
    check("import" not in frame.header.buttons and "export" not in frame.header.buttons,
          "Header no longer carries the test file commands")
    name_rect = frame.tests.details.heading.GetScreenRect()
    import_rect = files["import"].GetScreenRect()
    export_rect = files["export"].GetScreenRect()
    check(import_rect.x >= name_rect.GetRight(), "Import button is to the right of the test name")
    check(export_rect.x >= import_rect.GetRight(), "Export button sits beside the import button")
    check(abs(import_rect.y - export_rect.y) <= 2, "Both file buttons share one row")
    check(frame.tests.details.GetScreenRect().Contains(export_rect),
          "File buttons stay inside the details pane at 1024 DIP")
    check(files["import"].IsEnabled() and files["export"].IsEnabled(),
          "Both file buttons are usable for a saved test")
    check(main.scenario is not None and main.scenario.runnable,
          "Selected test reports a runnable scenario")
    check(frame.header.buttons["open"].IsEnabled(), "Start is enabled while a scenario exists")

    outbox = OUTPUT / "smoke-export"
    written = main.service.export(main.definition.id, outbox)
    check(written.is_dir() and sorted(p.name for p in written.iterdir())
          == ["target.csv", "test.json"],
          "Export copies the whole profile folder with both files inside")
    folder = main.service.folder_of(main.definition.id)
    check(folder.name == main.definition.name,
          "The profile folder name is the test name")

    scenario_path = main.service.scenario_path(main.definition.id)
    backup = scenario_path.read_text(encoding="utf-8")
    main.service.clear_scenario(main.definition.id)
    main.refresh_scenario()
    frame.render()
    # 시나리오는 선택이다. 없어도 시작할 수 있어야 한다.
    check(main.scenario.state == "none" and frame.header.buttons["open"].IsEnabled(),
          "Start stays enabled without a scenario (targets are optional)")
    check("목표값 없이 실행" in frame.GetStatusBar().GetStatusText(),
          "Status line says the run proceeds without targets")
    check(pane.buttons["clear"].IsEnabled() is False, "Clear is disabled when there is no scenario")

    # 반면 읽지 못하는 파일은 막아야 한다. 없음과 오류를 구별하는지 확인한다.
    scenario_path.write_text("time,target_v\n0,bad\n", encoding="utf-8")
    main.refresh_scenario()
    frame.render()
    check(main.scenario.state == "error" and not frame.header.buttons["open"].IsEnabled(),
          "Broken scenario disables Start instead of running with unintended targets")
    check("읽을 수 없습니다" in frame.GetStatusBar().GetStatusText(),
          "Status line explains the scenario cannot be read")

    scenario_path.write_text(backup, encoding="utf-8", newline="")
    main.refresh_scenario()
    frame.render()
    check(main.scenario.runnable and frame.header.buttons["open"].IsEnabled(),
          "Restoring the scenario re-enables Start")


def prepared():
    exp = frame.experiment
    assert exp is not None
    check(exp is not None, "Experiment opens in separate frame")
    buttons = exp.header.buttons
    check("copy" not in buttons and "results" not in buttons,
          "Experiment toolbar omits data copy and results dialog")
    check("charts" in buttons and buttons["charts"].icon is not None,
          "Result chart button reuses the former results icon")
    frame.experiment.start()
    later(running, 1200)


def running():
    check(experiment.sessions.view().state == State.RUNNING, "Acquisition runs while GUI responds")
    capture(frame.experiment, "experiment-1280")
    check_map()
    exp = frame.experiment
    assert exp is not None
    from unittest.mock import patch
    header = exp.header
    header.render("측정 중", "active", "00:08")
    positions = {key: button.GetRect() for key, button in header.buttons.items()}
    with patch.object(header, "Layout", wraps=header.Layout) as layout, \
            patch.object(header.state, "Refresh", wraps=header.state.Refresh) as refresh:
        for clock in ("00:09", "00:10", "01:00", "99:59"):
            header.render("측정 중", "active", clock)
        check(layout.call_count == 0, "Clock ticks do not relayout the header")
        check(refresh.call_count == 0, "Unchanged status chip does not request repaint")
    check(all(button.GetRect() == positions[key] for key, button in header.buttons.items()),
          "Clock updates preserve toolbar button geometry")
    header.render("측정 중", "active", "100:00")
    check(header.clock.GetSize().width >= header.clock.GetTextExtent("100:00").width,
          "Clock grows for elapsed times beyond 99 minutes")
    exp.tick()
    models = experiment.metrics(experiment.telemetry.snapshot())
    cards = {channel: card for pane in exp.sensors.values() for channel, card in pane.cards.items()}
    check(list(cards) == ["B.0", "B.1", "C.0", "C.1"], "Four metric cards appear in row order")
    check(all(card.label.GetLabel() == models[channel].label for channel, card in cards.items()),
          "Metric cards show their channel labels")
    content = experiment.copy()
    check("session_id\ttest_id" in content and "gps\tlatitude" in content, "Latest displayed values serialize to TSV")
    frame.experiment.SetClientSize(frame.experiment.FromDIP((1024, 768)))
    later(narrow_experiment)


def narrow_experiment():
    exp = frame.experiment
    stop = exp.header.buttons["stop"]
    rect = stop.GetScreenRect()
    check(exp.GetScreenRect().Contains(rect), "Stop button stays inside 1024 DIP window")
    # 수집은 스스로 끝나지 않는다. 중지를 누르기 전까지 살아 있어야 한다.
    check(experiment.sessions.current.state == State.RUNNING,
          "Session keeps running until the user stops it")
    check(experiment.sessions.sensors.continuous,
          "Demo source declares itself continuous (no fixed-length cutoff)")
    check("중지를 누를 때까지" in exp.GetStatusBar().GetStatusText(),
          "Status line promises collection until stop, not a fixed 30 seconds")
    for pane in exp.sensors.values():
        for card in pane.cards.values():
            check(exp.GetClientRect().Contains(exp.ScreenToClient(card.GetScreenPosition()))
                  and exp.GetClientRect().Contains(exp.ScreenToClient(card.GetScreenRect().GetBottomRight())),
                  "Metric card remains inside 1024 DIP client area")
    capture(exp, "experiment-1024")
    check_experiment_touch_scroll(exp)
    exp.stop()
    later(stopped)


def stopped():
    if not experiment.sessions.is_idle():
        later(stopped)
        return
    check(experiment.sessions.view().state == State.STOPPED, "Stop waits for recording finalization")
    exp = frame.experiment
    assert exp is not None
    check(exp.header.buttons["charts"].IsEnabled(), "Result chart is available after recording closes")
    exp.toggle_charts()
    check(exp.showing_charts and exp.header.buttons["charts"].GetLabel() == "측정 화면",
          "Result chart replaces the measurement body")
    check_chart_touch_scroll(exp)
    exp.toggle_charts()
    check(not exp.showing_charts and exp.header.buttons["charts"].GetLabel() == "결과 그래프",
          "Result chart returns to the measurement body")
    exp.request_close(confirm=False)
    check(frame.experiment is None, "Experiment close releases frame reference and timer")
    finish()


def finish():
    if frame.experiment:
        experiment.sessions.stop(experiment.sessions.view().session_id)
        if experiment.sessions.worker:
            experiment.sessions.worker.join(4)
        exp = frame.experiment
        exp.dispose()
        exp.Destroy()
    frame.dispose()
    frame.Destroy()
    (OUTPUT / "gui-smoke.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    app.ExitMainLoop()


later(start, 700)
app.MainLoop()
print(json.dumps(report, ensure_ascii=False, indent=2))
sys.exit(bool(report["errors"]))
