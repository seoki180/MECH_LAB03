import wx
from experiment_app.domain.session import State, ACTIVE, LABELS
from experiment_app.domain.test_definition import AppError
from experiment_app.domain.edit_policy import EditPolicy
from experiment_app.ui.theme import style
from experiment_app.ui.components.header import Header
from experiment_app.ui.pages.tests_page import TestsPage
from experiment_app.ui.dialogs.settings import SettingsDialog
from experiment_app.ui.dialogs.results import ResultsDialog
from .experiment_frame import ExperimentFrame


class MainFrame(wx.Frame):
    def __init__(self, presenter, experiment_presenter, clipboard, tiles, fields, scenarios):
        super().__init__(None, title="MECHLab 실험 관리 (데모)")
        self.presenter, self.experiment_presenter, self.clipboard = presenter, experiment_presenter, clipboard
        self.tiles = tiles
        self.repository = presenter.service.repository
        self.sessions = experiment_presenter.sessions
        self.scenarios = scenarios
        self.settings_dialog = None
        self.result_dialog = None
        self.selected_group = next(iter(self.repository.groups()), None)
        self.experiment = None
        self.closing = False
        self.disposed = False
        style(self)
        self.SetClientSize(self.FromDIP((1280, 800)))
        self.SetMinSize(self.FromDIP((600, 500)))
        root = wx.BoxSizer(wx.VERTICAL)
        self.header = Header(self, "MECHLab / 데모 차량", (
            ("add", "추가", self.add_item, False), ("duplicate", "복제", self.duplicate, False),
            ("delete", "삭제", self.delete, False), None,
            ("edit", "수정", self.begin_edit, False), ("cancel", "취소", self.cancel_edit, False), ("save", "저장", self.save, True), None,
            ("open", "시험시작", self.open_experiment, True), ("show", "실험 창 보기", self.show_experiment, False), None,
            ("settings", "설정", self.open_settings, False),
        ), compact=True, status_chips=False)
        root.Add(self.header, 0, wx.EXPAND)
        self.tests = TestsPage(self, fields, self.select, self.reorder, self.patch, self.structure,
                               {"import": self.import_test, "export": self.export_test,
                                "scenario_import": self.import_scenario,
                                "scenario_clear": self.clear_scenario})
        root.Add(self.tests, 1, wx.EXPAND)
        self.SetSizer(root)
        self.CreateStatusBar()
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.tick, self.timer)
        self.Bind(wx.EVT_CLOSE, self.close)
        self.timer.Start(100)
        definitions = self.repository.list()
        if definitions:
            self.presenter.select(definitions[0].id)
        self.render()

    def error(self, error):
        self.SetStatusText(f"{getattr(error, 'code', 'ERROR')}: {error}")
        if isinstance(error, AppError) and error.errors:
            self.presenter.errors = error.errors
            self.tests.details.update_draft({**self.presenter.definition.fields(), **self.presenter.changes}, error.errors)
            self.tests.details.focus_error(error.errors)
        else:
            wx.MessageBox(str(error), getattr(error, "code", "오류"), wx.OK | wx.ICON_ERROR, self)

    def render(self):
        p = self.presenter
        definition = p.definition
        self.tests.tree.render(self.repository.groups(), self.repository.list(), definition.id if definition else self.selected_group)
        policy = (p.service.policy_provider(definition) if p.editing else
                  EditPolicy("readonly", frozenset(), "수정 버튼을 눌러 편집하세요.")) if definition else None
        group_label = self.repository.groups().get(self.selected_group) if not definition else None
        self.tests.details.render(definition, p.changes, policy, p.errors, group_label,
                                  p.scenario, not p.busy, not p.busy)
        self.tick()

    # ------------------------------------------------ 시험 파일과 시험시나리오

    def import_scenario(self):
        """CSV를 골라 현재 시험의 시험시나리오로 삼는다."""
        definition = self.presenter.definition
        if definition is None or self.presenter.busy:
            return
        if definition.revision == 0:
            self.error(AppError("VALIDATION_FAILED", "시험을 먼저 저장한 뒤 시나리오를 가져오세요."))
            return
        dialog = wx.FileDialog(self, "시험시나리오 CSV 선택", wildcard="시나리오 CSV (*.csv)|*.csv",
                               style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        result = dialog.ShowModal()
        path = dialog.GetPath()
        dialog.Destroy()
        if result != wx.ID_OK:
            return
        def done(points, error):
            if error:
                self.error(error)
                return
            self.presenter.refresh_scenario()
            self.render()
            self.SetStatusText(f"시험시나리오 {len(points)}행을 가져왔습니다.")
        self.presenter.import_scenario(path, done)
        self.tick()

    def clear_scenario(self):
        definition = self.presenter.definition
        if definition is None or self.presenter.busy or definition.revision == 0:
            return
        dialog = wx.MessageDialog(self, "이 시험의 시험시나리오 CSV를 지웁니다.\n시나리오 없이도 시험을 시작할 수 있습니다.",
                                  "시나리오 비우기", wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
        result = dialog.ShowModal()
        dialog.Destroy()
        if result != wx.ID_YES:
            return
        def done(_, error):
            if error:
                self.error(error)
                return
            self.presenter.refresh_scenario()
            self.render()
            self.SetStatusText("시험시나리오를 비웠습니다.")
        self.presenter.clear_scenario(done)
        self.tick()

    def export_test(self):
        """현재 시험 폴더를 통째로 고른 위치에 복사한다."""
        definition = self.presenter.definition
        if definition is None or self.presenter.busy:
            return
        if definition.revision == 0:
            self.error(AppError("VALIDATION_FAILED", "시험을 먼저 저장한 뒤 내보내세요."))
            return
        dialog = wx.DirDialog(self, "시험 폴더를 복사할 위치 선택", style=wx.DD_DEFAULT_STYLE)
        result = dialog.ShowModal()
        path = dialog.GetPath()
        dialog.Destroy()
        if result != wx.ID_OK:
            return
        def done(written, error):
            if error:
                self.error(error)
                return
            self.SetStatusText(f"내보냈습니다: {written.name} 폴더")
            self.tick()
        self.presenter.export(path, done)
        self.tick()

    def import_test(self):
        """외부 시험 폴더를 선택한 시험목록으로 통째로 들여온다."""
        if self.presenter.busy:
            return
        # 버튼이 시험 이름 옆에 있으므로 보고 있는 시험의 시험목록을 기본 대상으로 삼는다.
        current = self.presenter.definition
        group = (current.group_id if current else self.selected_group) \
            or next(iter(self.repository.groups()), None)
        if group is None:
            self.error(AppError("VALIDATION_FAILED", "먼저 시험목록을 추가하세요."))
            return
        def action():
            dialog = wx.DirDialog(self, f"'{self.repository.groups().get(group, group)}'(으)로 가져올 시험 폴더",
                                  style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST)
            result = dialog.ShowModal()
            path = dialog.GetPath()
            dialog.Destroy()
            if result != wx.ID_OK:
                return
            def done(definition, error):
                if error:
                    self.error(error)
                    return
                self.presenter.select(definition.id)
                self.selected_group = definition.group_id
                self.render()
                self.SetStatusText(f"가져왔습니다: {definition.name}")
            self.presenter.import_test(path, group, done)
            self.tick()
        self.guard_dirty(action)

    def open_settings(self):
        if self.settings_dialog is not None or self.closing:
            return
        self.settings_dialog = SettingsDialog(self, self.scenarios, self.set_scenario,
                                              self.select_nmea, self.experiment_presenter.nmea_path,
                                              self.sessions.scenario)
        try:
            self.settings_dialog.ShowModal()
        finally:
            self.settings_dialog.Destroy()
            self.settings_dialog = None

    def begin_edit(self):
        self.presenter.begin_edit()
        self.render()

    def cancel_edit(self):
        self.presenter.cancel_edit()
        self.render()

    def patch(self, path, value):
        self.presenter.edit(path, value)
        self.tests.details.update_draft({**self.presenter.definition.fields(), **self.presenter.changes}, self.presenter.errors)
        self.tick()

    def structure(self, operation, step_id):
        try:
            self.presenter.structure(operation, step_id)
            self.render()
        except AppError as error:
            self.error(error)

    def guard_dirty(self, proceed):
        if self.presenter.busy:
            return
        if not self.presenter.dirty:
            proceed()
            return
        dialog = wx.MessageDialog(self, "현재 시험에 저장하지 않은 변경이 있습니다.", "변경 내용 처리", wx.YES_NO | wx.CANCEL)
        dialog.SetYesNoCancelLabels("저장", "변경 버리기", "취소")
        result = dialog.ShowModal()
        dialog.Destroy()
        if result == wx.ID_YES:
            self.save(proceed)
        elif result == wx.ID_NO:
            definition = self.presenter.definition
            self.presenter.select(definition.id if definition.revision else None)
            proceed()

    def select(self, kind, key):
        if kind == "test" and self.presenter.definition and self.presenter.definition.id == key:
            self.tests.selection_done()
            return
        def action():
            self.presenter.select(key if kind == "test" else None)
            self.selected_group = self.presenter.definition.group_id if kind == "test" else key
            self.render()
            if kind == "test":
                self.tests.selection_done()
        self.guard_dirty(action)

    def save(self, after=None):
        if self.presenter.busy:
            return
        if not self.presenter.dirty:
            self.presenter.end_edit()
            self.render()
            if after:
                after()
            return
        def done(result, error):
            if error:
                self.error(error)
                self.tick()
                return
            self.render()
            self.SetStatusText(f"저장했습니다. 리비전 {result.revision}")
            if after:
                after()
        try:
            self.presenter.save(done)
            self.tick()
        except AppError as error:
            self.error(error)

    def add_item(self):
        def action():
            dialog = wx.SingleChoiceDialog(self, "추가할 항목을 선택하세요.", "추가", ["시험", "시험목록"])
            result = dialog.ShowModal()
            kind = dialog.GetSelection()
            dialog.Destroy()
            if result != wx.ID_OK:
                return
            if kind == 0:
                group = self.selected_group or next(iter(self.repository.groups()), None)
                if group is None:
                    self.error(AppError("VALIDATION_FAILED", "먼저 시험목록을 추가하세요."))
                    return
                self.presenter.new(group)
                self.tests.selection_done()
                self.render()
            else:
                dialog = wx.TextEntryDialog(self, "시험목록 이름", "시험목록 추가")
                result = dialog.ShowModal()
                name = dialog.GetValue().strip()
                dialog.Destroy()
                if result == wx.ID_OK and name:
                    self.presenter.submit(lambda: self.repository.add_group(name), self._group_added)
        self.guard_dirty(action)

    def _group_added(self, group_id, error):
        if error:
            self.error(error)
            return
        self.selected_group = group_id
        self.presenter.select(None)
        self.render()

    def duplicate(self):
        if not self.presenter.definition:
            return
        original = self.presenter.definition
        def action():
            if self.presenter.definition is None:
                self.presenter.definition = original
            self.presenter.new(self.presenter.definition.group_id, duplicate=True)
            self.render()
        self.guard_dirty(action)

    def delete(self):
        definition = self.presenter.definition
        group_id = self.selected_group
        def action():
            name = definition.name if definition else self.repository.groups().get(group_id, "")
            count = 1 if definition else len([d for d in self.repository.list() if d.group_id == group_id])
            dialog = wx.MessageDialog(self, f'“{name}” 및 시험 {count}개를 삭제합니다.\n실행 snapshot과 과거 결과는 보존됩니다.', "삭제 확인", wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
            result = dialog.ShowModal()
            dialog.Destroy()
            if result != wx.ID_YES:
                return
            old = self.repository.list()
            index = next((i for i, d in enumerate(old) if definition and d.id == definition.id), 0)
            def done(result, error):
                if error:
                    self.error(error)
                    return
                remaining = self.repository.list()
                self.presenter.select(remaining[min(index, len(remaining) - 1)].id if remaining else None)
                self.selected_group = self.presenter.definition.group_id if self.presenter.definition else next(iter(self.repository.groups()), None)
                self.render()
            self.presenter.submit(lambda: self.repository.delete(definition.id) if definition else self.repository.delete_group(group_id), done)
        self.guard_dirty(action)

    def reorder(self, offset):
        if self.tests.tree.search.GetValue().strip() or not self.presenter.definition:
            return
        self.guard_dirty(lambda: self.presenter.submit(lambda: self.repository.reorder(self.presenter.definition.id, offset), self._mutation_done))

    def _mutation_done(self, result, error):
        if error:
            self.error(error)
        else:
            self.render()

    def open_experiment(self):
        if not self.presenter.definition or self.presenter.busy:
            return
        if self.presenter.dirty:
            dialog = wx.MessageDialog(self, "실험은 저장된 설정의 snapshot으로 실행합니다.", "저장 후 실험 열기", wx.YES_NO | wx.NO_DEFAULT)
            dialog.SetYesNoLabels("저장 후 열기", "취소")
            result = dialog.ShowModal()
            dialog.Destroy()
            if result == wx.ID_YES:
                self.save(self._prepare)
            return
        self._prepare()

    def _prepare(self):
        definition = self.presenter.definition
        current = self.sessions.view()
        if self.experiment:
            if current.snapshot.definition.id == definition.id:
                self.show_experiment()
                return
            if current.state in ACTIVE:
                self.error(AppError("INVALID_STATE", "측정 중입니다. 기존 실험을 종료한 뒤 다른 시험을 여세요."))
                return
            dialog = wx.MessageDialog(self, "기존 실험 창을 닫고 선택한 시험을 준비할까요?", "준비 세션 교체", wx.YES_NO | wx.NO_DEFAULT)
            if dialog.ShowModal() != wx.ID_YES:
                dialog.Destroy()
                return
            dialog.Destroy()
            if not self.sessions.is_idle():
                return
            self.experiment.request_close(confirm=False)
        try:
            self.sessions.prepare(definition.id, definition.revision, replace_ready=True)
        except AppError as error:
            self.error(error)
            return
        self.experiment = ExperimentFrame(self, self.experiment_presenter, self.tiles,
                                          self.experiment_closed)
        self.experiment.Show()

    def experiment_closed(self):
        self.experiment = None

    def show_experiment(self):
        if self.experiment:
            self.experiment.Show()
            self.experiment.Raise()

    def show_results(self, session_id):
        if self.result_dialog is not None or self.closing:
            return
        self.result_dialog = ResultsDialog(self.experiment or self, self.sessions.results.list(), session_id)
        try:
            self.result_dialog.ShowModal()
        finally:
            self.result_dialog.Destroy()
            self.result_dialog = None

    def set_scenario(self, scenario):
        self.sessions.scenario = scenario
        self.repository.fail_save = scenario == "저장 실패"

    def select_nmea(self, path):
        if self.experiment:
            self.error(AppError("INVALID_STATE", "실험 창을 닫은 뒤 NMEA 파일을 선택하세요."))
            return False
        try:
            self.experiment_presenter.select_nmea(path)
        except AppError as error:
            self.error(error)
            return False
        return True

    def tick(self, event=None):
        if self.disposed:
            return
        self.presenter.poll()
        if self.disposed:
            return
        p = self.presenter
        session = self.sessions.view()
        if self.experiment and session:
            tone = "active" if session.state in ACTIVE else "idle"
            self.header.render("실험 " + LABELS[session.state], tone)
        else:
            self.header.render("실험 창 닫힘")
        for key, control in self.header.buttons.items():
            control.Show(True if key != "show" else bool(self.experiment))
        self.header.buttons["save"].Enable((p.editing or (p.definition is not None and p.definition.revision == 0))
                                           and not p.errors and not p.busy)
        self.header.buttons["edit"].Enable(p.definition is not None and not p.editing and not p.busy)
        self.header.buttons["cancel"].Enable(p.editing and not p.busy)
        for key in ("duplicate", "open"):
            self.header.buttons[key].Enable(p.definition is not None and not p.busy)
        # 시나리오 파일이 깨진 경우에만 시작을 막는다. 시나리오가 없는 것은 선택이므로
        # 목표값 없이 실행할 수 있다. 버튼을 누른 뒤 실패하는 대신 미리 막는다.
        runnable = p.scenario is not None and p.scenario.runnable
        self.header.buttons["open"].Enable(p.definition is not None and not p.busy and runnable)
        self.header.buttons["delete"].Enable(bool(p.definition or self.selected_group) and not p.busy)
        self.header.buttons["add"].Enable(not p.busy)
        self.tests.Enable(not p.busy)
        self.header.Layout()
        self.Layout()
        self.SetStatusText("저장 중…" if p.busy else ("입력 오류가 있습니다. 빨간 안내가 붙은 항목을 고치세요." if p.errors else
                           ("수정했지만 아직 저장하지 않았습니다." if p.dirty else
                            ("시험시나리오 CSV를 읽을 수 없습니다. 파일을 고치거나 비운 뒤 시작하세요."
                             if p.definition is not None and not runnable else
                             ("목표값 없이 실행합니다. 저장된 설정입니다. 데모 모드로 실행 중입니다."
                              if p.scenario is not None and p.scenario.state == "none" else
                              "저장된 설정입니다. 데모 모드로 실행 중입니다.")))))
        if self.closing and not self.experiment and self.sessions.is_idle() and not p.busy:
            self.dispose()
            self.Destroy()

    def close(self, event):
        if event.CanVeto():
            event.Veto()
        if self.closing:
            return
        def action():
            if self.experiment and not self.experiment.request_close():
                return
            self.closing = True
            self.tick()
        self.guard_dirty(action)

    def dispose(self):
        if self.disposed:
            return
        self.disposed = True
        self.timer.Stop()
        self.tests.dispose()
        self.presenter.dispose()
