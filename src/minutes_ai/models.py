"""抽出結果のデータモデルと、Claude に指定する JSON スキーマ."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Item:
    """決定事項・未決事項の 1 件. quote は原文からの引用 (根拠)."""

    text: str
    quote: str
    grounded: bool | None = None  # quote が原文に実在するか. grounding.check で設定


@dataclass
class ActionItem:
    owner: str
    task: str
    due: str | None  # YYYY-MM-DD. 期限がなければ None
    quote: str
    grounded: bool | None = None


@dataclass
class Minutes:
    summary: str
    decisions: list[Item] = field(default_factory=list)
    action_items: list[ActionItem] = field(default_factory=list)
    open_issues: list[Item] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Minutes:
        return cls(
            summary=data["summary"],
            decisions=[Item(d["text"], d["quote"]) for d in data["decisions"]],
            action_items=[
                ActionItem(a["owner"], a["task"], a["due"], a["quote"])
                for a in data["action_items"]
            ],
            open_issues=[Item(o["text"], o["quote"]) for o in data["open_issues"]],
        )

    def all_items(self) -> list[Item | ActionItem]:
        return [*self.decisions, *self.action_items, *self.open_issues]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _item_schema(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    properties: dict[str, Any] = dict(extra or {})
    properties["quote"] = {
        "type": "string",
        "description": "根拠となる原文の一部. 原文の文字列をそのままコピーする (翻訳・要約しない)",
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "会議・問い合わせの要約 (2〜3 文)"},
        "decisions": {
            "type": "array",
            "items": _item_schema({"text": {"type": "string"}}),
        },
        "action_items": {
            "type": "array",
            "items": _item_schema(
                {
                    "owner": {"type": "string", "description": "担当者名. 原文の表記のまま"},
                    "task": {"type": "string"},
                    "due": {
                        "anyOf": [{"type": "string", "description": "YYYY-MM-DD"}, {"type": "null"}]
                    },
                }
            ),
        },
        "open_issues": {
            "type": "array",
            "items": _item_schema({"text": {"type": "string"}}),
        },
    },
    "required": ["summary", "decisions", "action_items", "open_issues"],
    "additionalProperties": False,
}
