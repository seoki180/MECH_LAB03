from dataclasses import replace
from copy import deepcopy
from uuid import uuid4
from experiment_app.domain.edit_policy import EditPolicy
from experiment_app.domain.test_definition import AppError, utc_now


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
        # 복제는 새 시험이므로 생성일자도 새로 찍는다. 원본의 값만 물려받는다.
        return replace(definition, id=uuid4().hex, revision=0,
                       group_id=group_id or definition.group_id, name=definition.name + " 복사",
                       created_utc=utc_now(),
                       experiment_data=deepcopy(definition.experiment_data),
                       ar_trapezoidal_step=deepcopy(definition.ar_trapezoidal_step),
                       pf_straight_line=deepcopy(definition.pf_straight_line))

    # -------------------------------------------------- 시험 파일과 시험시나리오

    def scenario(self, test_id):
        """시험시나리오(time,target_v). 파일이 없으면 빈 열."""
        return self.repository.scenario(test_id)

    def scenario_path(self, test_id):
        return self.repository.scenario_path(test_id)

    def set_scenario(self, test_id, source):
        return self.repository.set_scenario(test_id, source)

    def clear_scenario(self, test_id):
        self.repository.clear_scenario(test_id)

    def export(self, test_id, destination):
        """시험 profile 폴더를 통째로 지정한 위치에 복사한다."""
        return self.repository.export(test_id, destination)

    def import_test(self, source, group_id):
        """외부 시험 폴더를 시험목록으로 통째로 들여온다."""
        return self.repository.import_test(source, group_id)

    def folder_of(self, test_id):
        return self.repository.folder_of(test_id)
