"""正解データ (*.gold.json) との比較による抽出精度の評価.

正解の各項目は、表現の揺れを許すためにキーワード (いずれか 1 つを含めば一致) で定義する。
タスクは担当者 (表記の別名を含む) とキーワードの両方が一致したときに正解とみなし、
一致したタスクについて期限が正しいかを別に数える。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from minutes_ai.extract import Extractor
from minutes_ai.grounding import normalize
from minutes_ai.models import ActionItem, Item, Minutes


def _contains_any(text: str, keywords: list[str]) -> bool:
    t = normalize(text)
    return any(normalize(k) in t for k in keywords)


def _owner_matches(owner: str, aliases: list[str]) -> bool:
    o = normalize(owner)
    return any(normalize(a) in o for a in aliases)


@dataclass
class Score:
    """1 種類の項目 (決定事項など) の一致数."""

    matched: int = 0
    predicted: int = 0
    expected: int = 0

    def add(self, other: Score) -> None:
        self.matched += other.matched
        self.predicted += other.predicted
        self.expected += other.expected

    @property
    def precision(self) -> float:
        return self.matched / self.predicted if self.predicted else 1.0

    @property
    def recall(self) -> float:
        return self.matched / self.expected if self.expected else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0


@dataclass
class MeetingResult:
    name: str
    decisions: Score
    action_items: Score
    open_issues: Score
    due_correct: int
    grounded: int
    items: int
    misses: list[str] = field(default_factory=list)


def _match_items(predicted: list[Item], gold: list[dict], label: str, misses: list[str]) -> Score:
    used: set[int] = set()
    for g in gold:
        hit = next(
            (
                i
                for i, p in enumerate(predicted)
                if i not in used and _contains_any(p.text, g["keywords"])
            ),
            None,
        )
        if hit is None:
            misses.append(f"{label}の見落とし: {g['keywords']}")
        else:
            used.add(hit)
    for i, p in enumerate(predicted):
        if i not in used:
            misses.append(f"{label}の余分な抽出: {p.text}")
    return Score(len(used), len(predicted), len(gold))


def _match_actions(
    predicted: list[ActionItem], gold: list[dict], misses: list[str]
) -> tuple[Score, int]:
    used: set[int] = set()
    due_correct = 0
    for g in gold:
        hit = next(
            (
                i
                for i, p in enumerate(predicted)
                if i not in used
                and _owner_matches(p.owner, g["owner"])
                and _contains_any(p.task, g["keywords"])
            ),
            None,
        )
        if hit is None:
            misses.append(f"タスクの見落とし: {g['owner'][0]} / {g['keywords']}")
            continue
        used.add(hit)
        if predicted[hit].due == g["due"]:
            due_correct += 1
        else:
            misses.append(f"期限の誤り: {g['owner'][0]} 正解={g['due']} 抽出={predicted[hit].due}")
    for i, p in enumerate(predicted):
        if i not in used:
            misses.append(f"タスクの余分な抽出: {p.owner} / {p.task}")
    return Score(len(used), len(predicted), len(gold)), due_correct


def score_meeting(name: str, minutes: Minutes, gold: dict) -> MeetingResult:
    misses: list[str] = []
    decisions = _match_items(minutes.decisions, gold["decisions"], "決定事項", misses)
    actions, due_correct = _match_actions(minutes.action_items, gold["action_items"], misses)
    open_issues = _match_items(minutes.open_issues, gold["open_issues"], "未決事項", misses)
    items = minutes.all_items()
    for item in items:
        if not item.grounded:
            misses.append(f"根拠が原文にない: {item.quote}")
    return MeetingResult(
        name=name,
        decisions=decisions,
        action_items=actions,
        open_issues=open_issues,
        due_correct=due_correct,
        grounded=sum(bool(i.grounded) for i in items),
        items=len(items),
        misses=misses,
    )


def evaluate(data_dir: str | Path, extractor: Extractor) -> list[MeetingResult]:
    results = []
    for gold_path in sorted(Path(data_dir).glob("*.gold.json")):
        name = gold_path.name.removesuffix(".gold.json")
        gold = json.loads(gold_path.read_text(encoding="utf-8"))
        text = gold_path.with_name(f"{name}.txt").read_text(encoding="utf-8")
        minutes = extractor.extract(text, gold["meeting_date"], gold["output_lang"])
        results.append(score_meeting(name, minutes, gold))
    return results


def summarize(results: list[MeetingResult]) -> str:
    totals = {"決定事項": Score(), "タスク": Score(), "未決事項": Score()}
    for r in results:
        totals["決定事項"].add(r.decisions)
        totals["タスク"].add(r.action_items)
        totals["未決事項"].add(r.open_issues)
    lines = ["| 項目 | 適合率 | 再現率 | F1 |", "|---|---|---|---|"]
    for label, s in totals.items():
        lines.append(
            f"| {label} | {s.matched}/{s.predicted} ({s.precision:.0%}) | "
            f"{s.matched}/{s.expected} ({s.recall:.0%}) | {s.f1:.2f} |"
        )
    matched = totals["タスク"].matched
    due = sum(r.due_correct for r in results)
    grounded = sum(r.grounded for r in results)
    items = sum(r.items for r in results)
    lines += [
        "",
        f"- 期限の正解率（一致したタスクのうち）: {due}/{matched} ({due / matched if matched else 0:.0%})",
        f"- 根拠の実在率（引用が原文に存在）: {grounded}/{items} ({grounded / items if items else 0:.0%})",
    ]
    errors = [(r.name, m) for r in results for m in r.misses]
    if errors:
        lines += ["", "| 会議 | 誤り |", "|---|---|"]
        lines += [f"| {name} | {m} |" for name, m in errors]
    return "\n".join(lines)
