from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4
from experiment_app.domain.test_definition import AppError, FieldPatch


class MainPresenter:
    """Owns draft strings and asynchronous persistence, without any wx dependencies."""
    def __init__(self, service, new_template):
        self.service = service
        self.new_template = new_template
        self.definition = None
        self.changes = {}
        self.errors = {}
        self.structure_dirty = False
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mechlab-storage")
        self.pending = None
        self.editing = False
        self.edit_baseline = None

    @property
    def busy(self):
        return self.pending is not None

    @property
    def dirty(self):
        return self.definition is not None and (bool(self.changes) or self.structure_dirty or self.definition.revision == 0)

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
            self.changes, self.errors, self.structure_dirty = {}, {}, False
            self.end_edit()

    def select(self, test_id):
        self.end_edit()
        self.definition = self.service.repository.get(test_id) if test_id else None
        self.changes, self.errors, self.structure_dirty = {}, {}, False

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
        source = self.definition if duplicate else self.new_template
        self.definition = self.service.duplicate(source, group_id)
        if not duplicate:
            self.definition = replace(self.definition, name="새 시험")
        self.changes, self.errors, self.structure_dirty = {}, {}, False

    def structure(self, operation, step_id):
        if self.definition is None or self.busy or not self.editing:
            return
        if self.service.policy_provider(self.definition).context != "full":
            raise AppError("VALIDATION_FAILED", "단계 편집이 잠겨 있습니다.")
        steps = list(self.definition.spec_items)
        index = next((i for i, s in enumerate(steps) if s.id == step_id), 0)
        if operation in {"add", "duplicate"}:
            source = steps[index] if operation == "duplicate" else self.new_template.spec_items[0]
            steps.insert(index + 1, replace(source, id=uuid4().hex, name=f"단계 {len(steps) + 1}"))
        elif operation == "delete" and len(steps) > 1:
            removed = steps.pop(index)
            self.changes = {k: v for k, v in self.changes.items() if not k.startswith(f"spec/{removed.id}/")}
            self.errors = {k: v for k, v in self.errors.items() if not k.startswith(f"spec/{removed.id}/")}
        elif operation in {"up", "down"}:
            target = index + (-1 if operation == "up" else 1)
            if 0 <= target < len(steps):
                steps[index], steps[target] = steps[target], steps[index]
        self.definition = replace(self.definition, spec_items=tuple(steps))
        self.structure_dirty = True

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
        def action():
            if definition.revision == 0:
                return self.service.save_new(definition, changes, require_edit=self.editing)
            if self.structure_dirty:
                parsed = self.service.validate(definition, changes)
                return self.service.save_structure(definition.patched(parsed), definition.revision, require_edit=True)
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
            self.changes, self.errors, self.structure_dirty = {}, {}, False
        callback(result, None)

    def dispose(self):
        self.end_edit()
        self.executor.shutdown(wait=False, cancel_futures=True)
