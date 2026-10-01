from concurrent.futures import ThreadPoolExecutor
from experiment_app.domain.test_definition import AppError, FieldPatch, new_definition
from .view_models import scenario_model


class MainPresenter:
    """Owns draft strings and asynchronous persistence, without any wx dependencies."""
    def __init__(self, service):
        self.service = service
        self.definition = None
        self.changes = {}
        self.errors = {}
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mechlab-storage")
        self.pending = None
        self.editing = False
        self.edit_baseline = None
        # 선택한 시험의 시나리오 표시 상태(ScenarioViewModel). 시험이 없으면 None.
        self.scenario = None
        # 복제로 만든 초안이 저장될 때 따라 복사할 원본 시나리오 경로.
        self.scenario_source = None

    @property
    def busy(self):
        return self.pending is not None

    @property
    def dirty(self):
        return self.definition is not None and (bool(self.changes) or self.definition.revision == 0)

    def begin_edit(self):
        if self.definition is not None and not self.busy and not self.editing:
            self.editing = True
            self.edit_baseline = self.definition
            self.service.begin_edit(self.definition)

    def end_edit(self):
        self.editing = False
        self.edit_baseline = None
        self.service.end_edit()

    def cancel_edit(self):
        if self.editing and not self.busy:
            self.definition = self.edit_baseline
            self.changes, self.errors = {}, {}
            self.end_edit()

    def select(self, test_id):
        self.end_edit()
        self.definition = self.service.repository.get(test_id) if test_id else None
        self.changes, self.errors = {}, {}
        self.refresh_scenario()

    def refresh_scenario(self):
        """선택한 시험의 시나리오 상태를 다시 읽는다.

        파일 한 개를 읽을 뿐이라 선택 시점에 동기로 처리한다. 쓰기(가져오기/내보내기)는
        executor로 보낸다.
        """
        if self.definition is None or self.definition.revision == 0:
            self.scenario = None
            return self.scenario
        try:
            points = self.service.scenario(self.definition.id)
            # 안쪽 파일 이름은 고정이라 폴더 이름을 함께 보여야 어느 시험인지 알 수 있다.
            path = self.service.scenario_path(self.definition.id)
            self.scenario = scenario_model(points, file_name=f"{path.parent.name}/{path.name}")
        except AppError as error:
            self.scenario = scenario_model((), error=error)
        return self.scenario

    def import_scenario(self, source, callback):
        """CSV 파일을 현재 시험의 시나리오로 삼는다."""
        if self.definition is None or self.busy:
            return
        test_id = self.definition.id
        self.submit(lambda: self.service.set_scenario(test_id, source), callback)

    def clear_scenario(self, callback):
        if self.definition is None or self.busy:
            return
        test_id = self.definition.id
        self.submit(lambda: self.service.clear_scenario(test_id), callback)

    def export(self, destination, callback):
        """현재 시험 폴더를 통째로 지정한 위치에 복사한다."""
        if self.definition is None or self.busy:
            return
        test_id = self.definition.id
        self.submit(lambda: self.service.export(test_id, destination), callback)

    def import_test(self, source, group_id, callback):
        if self.busy:
            return
        self.submit(lambda: self.service.import_test(source, group_id), callback)

    def edit(self, path, raw_value):
        if self.busy or self.definition is None or not self.editing:
            return
        if raw_value == self.definition.fields().get(path):
            self.changes.pop(path, None)
        else:
            self.changes[path] = raw_value
        try:
            self.service.validate_edit(self.definition, self.changes)
            self.errors = {}
        except AppError as error:
            self.errors = error.errors

    def new(self, group_id, duplicate=False):
        self.end_edit()
        source = self.definition if duplicate else None
        # 복제는 시나리오도 따라가야 같은 시험이 된다. 원본 경로를 기억해 두었다가
        # 저장이 끝난 뒤 새 파일 옆으로 복사한다.
        self.scenario_source = (self.service.scenario_path(source.id)
                                if duplicate and source is not None and source.revision else None)
        # 새 시험은 어떤 값도 미리 채우지 않는다. 복제만 원본 값을 물려받는다.
        self.definition = (self.service.duplicate(source, group_id) if duplicate and source is not None
                           else new_definition(group_id))
        self.changes, self.errors = {}, {}
        self.scenario = None

    def save(self, callback):
        if self.busy or self.definition is None:
            return
        if not self.editing and self.definition.revision != 0:
            return
        if not self.dirty:
            self.end_edit()
            callback(self.definition, None)
            return
        if self.editing:
            self.service.validate_edit(self.definition, self.changes)
        else:
            self.service.validate(self.definition, self.changes)
        definition, changes = self.definition, dict(self.changes)
        scenario_source = self.scenario_source
        def action():
            if definition.revision == 0:
                saved = self.service.save_new(definition, changes, require_edit=self.editing)
                # 복제본은 원본 시나리오를 그대로 물려받는다.
                if scenario_source is not None and scenario_source.is_file():
                    self.service.set_scenario(saved.id, scenario_source)
                return saved
            return self.service.save_patch(FieldPatch(definition.id, definition.revision, changes), require_edit=True)
        self.submit(action, callback, saved=True)

    def submit(self, action, callback, saved=False):
        if not self.busy:
            self.pending = self.executor.submit(action), callback, saved

    def poll(self):
        if not self.pending or not self.pending[0].done():
            return
        future, callback, saved = self.pending
        self.pending = None
        try:
            result = future.result()
        except Exception as error:
            callback(None, error)
            return
        if saved:
            self.end_edit()
            self.definition = result
            self.changes, self.errors = {}, {}
            self.scenario_source = None
            self.refresh_scenario()
        callback(result, None)

    def dispose(self):
        self.end_edit()
        self.executor.shutdown(wait=False, cancel_futures=True)
