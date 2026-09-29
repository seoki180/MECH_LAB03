from dataclasses import replace
from copy import deepcopy
from uuid import uuid4
from experiment_app.domain.edit_policy import EditPolicy
from experiment_app.domain.test_definition import AppError


class TestService:
    def __init__(self, repository, schema_provider, policy_provider=EditPolicy.full):
        self.repository = repository
        self.schema_provider = schema_provider
        self.policy_provider = policy_provider
        self.editing_test_id = None

    def begin_edit(self, definition):
        self.editing_test_id = definition.id

    def end_edit(self):
        self.editing_test_id = None

    def validate_edit(self, definition, changes):
        if self.editing_test_id != definition.id:
            raise AppError("EDIT_LOCKED", "수정 버튼을 눌러 편집을 시작하세요.")
        return self.validate(definition, changes)

    def validate(self, definition, changes):
        policy = self.policy_provider(definition)
        schemas = self.schema_provider(definition)
        parsed, errors = {}, {}
        for path, value in changes.items():
            if path not in policy.editable_paths or path not in schemas:
                errors[path] = policy.locked_reason
                continue
            try:
                parsed[path] = schemas[path].parse(value)
            except ValueError as error:
                errors[path] = str(error)
        if errors:
            raise AppError("VALIDATION_FAILED", "입력 오류를 확인하세요.", errors)
        return parsed

    def save_patch(self, patch, *, require_edit=False):
        definition = self.repository.get(patch.test_id)
        if patch.base_revision != definition.revision:
            raise AppError("REVISION_CONFLICT", "저장 revision이 변경되었습니다. 현재 초안은 유지됩니다.")
        changes = (self.validate_edit if require_edit else self.validate)(definition, patch.changes)
        return self.repository.save(definition.patched(changes), patch.base_revision)

    def save_new(self, definition, changes, *, require_edit=False):
        changes = (self.validate_edit if require_edit else self.validate)(definition, changes)
        return self.repository.save(definition.patched(changes), 0)

    def duplicate(self, definition, group_id=None):
        return replace(definition, id=uuid4().hex, revision=0,
                       group_id=group_id or definition.group_id, name=definition.name + " 복사",
                       spec_items=tuple(replace(s, id=uuid4().hex) for s in definition.spec_items),
                       experiment_data=deepcopy(definition.experiment_data),
                       ar_trapezoidal_step=deepcopy(definition.ar_trapezoidal_step),
                       pf_straight_line=deepcopy(definition.pf_straight_line))

    def save_structure(self, definition, expected_revision, *, require_edit=False):
        if require_edit:
            self.validate_edit(definition, {})
        current = self.repository.get(definition.id)
        policy = self.policy_provider(current)
        if policy.context != "full":
            raise AppError("VALIDATION_FAILED", "단계 구조는 전체 편집 정책에서만 변경할 수 있습니다.")
        if not definition.spec_items:
            raise AppError("VALIDATION_FAILED", "최소 한 단계가 필요합니다.")
        self.validate(definition, {k: v for k, v in definition.fields().items() if k != "type_id"})
        return self.repository.save(definition, expected_revision)
