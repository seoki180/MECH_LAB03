from dataclasses import dataclass


@dataclass(frozen=True)
class EditPolicy:
    context: str
    editable_paths: frozenset[str]
    locked_reason: str = "현재 편집 정책에서 잠긴 항목입니다."

    @classmethod
    def full(cls, definition):
        return cls("full", frozenset(definition.fields()) - {"type_id"})

    @classmethod
    def readonly(cls):
        return cls("readonly", frozenset())
