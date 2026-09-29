"""Opt-in checks for the advanced-section toggle in the test details pane.

Run from the project root on a desktop session: uv run python tests/advanced_toggle_check.py
"""
import json
from pathlib import Path
import sys
import wx
from experiment_app.bootstrap import build_services
from experiment_app.demo.fixtures import BASIC_FIELDS, STEP_FIELDS
from experiment_app.domain.edit_policy import EditPolicy
from experiment_app.domain.test_definition import definition_from_dict
from experiment_app.ui.panes.test_details import TestDetailsPane
from experiment_app.ui.theme import load_fonts

OUTPUT = Path("artifacts")
OUTPUT.mkdir(exist_ok=True)
results = []


def check(condition, message):
    results.append((bool(condition), message))
    if not condition:
        raise AssertionError(message)


app = wx.App(False)
load_fonts()
main, _ = build_services(OUTPUT / "toggle-data", duration=5)
definition = main.service.repository.list()[0]
frame = wx.Frame(None)
frame.SetClientSize(frame.FromDIP((900, 800)))
pane = TestDetailsPane(frame, (BASIC_FIELDS, STEP_FIELDS), lambda path, value: None, lambda *a: None)
pane.render(definition, {}, EditPolicy.full(definition), {})
frame.Show()
frame.Layout()
wx.Yield()

spec = pane.spec
point_angle = spec.editors[0][0]
hidden = [editor for editor, _, _ in spec.editors[1:]]

# IsShownOnScreen walks the parent chain, so it reflects the collapsed container.
check(not spec.advanced_shown(), "Advanced sections start collapsed")
check(point_angle.IsShownOnScreen(), "Point Angle stays visible while collapsed")
check(all(not editor.IsShownOnScreen() for editor in hidden),
      "Calibration/Limit/Zero brake/robot sections are hidden while collapsed")
check(pane.advanced_toggle.GetLabel() == "고급 설정 펼치기", "Collapsed toggle invites expansion")

pane.toggle_advanced()
wx.Yield()
check(spec.advanced_shown(), "Toggle expands the advanced sections")
check(all(editor.IsShownOnScreen() for editor in hidden), "Every advanced section becomes visible")
check(pane.advanced_toggle.GetLabel() == "고급 설정 접기", "Expanded toggle invites collapsing")

pane.toggle_advanced()
wx.Yield()
check(not spec.advanced_shown(), "Toggle collapses the advanced sections again")
check(all(not editor.IsShownOnScreen() for editor in hidden), "Advanced sections hide again")

# A validation error inside a hidden section must open the toggle before focusing.
hidden_path = next(iter(spec.advanced_paths))
pane.focus_error({hidden_path: "확인이 필요합니다."})
check(spec.advanced_shown(), "Error in a hidden section expands the advanced toggle")

# The removed 메모 field must be gone from the editable surface and the saved model.
check(not any(path.startswith("advanced/") for path in definition.fields()),
      "Definition no longer exposes advanced/ note paths")
check(not hasattr(definition, "advanced_values"), "TestDefinition dropped advanced_values")

# Save files written before the removal still load.
legacy = json.loads(json.dumps({
    "id": "legacy", "group_id": "group-a", "revision": 3, "type_id": "demo",
    "name": "legacy", "runs": 1,
    "spec_items": [{"id": "s1", "name": "단계 1", "values": [["target", 1.0]],
                    "schema_id": "demo-step", "schema_version": 1}],
    "advanced_values": [["note", "옛 메모"]],
}))
restored = definition_from_dict(legacy)
check(restored.name == "legacy" and restored.revision == 3,
      "Legacy save file with advanced_values still loads")

print(json.dumps({"checks": [m for ok, m in results if ok]}, ensure_ascii=False, indent=2))
frame.Destroy()
app.Destroy()
sys.exit(0)
