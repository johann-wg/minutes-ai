"""モデルごとの抽出精度と費用を比較し、Markdown にまとめる.

キャッシュ済みの応答だけを使うため、API を呼ばず費用もかからない。

    python scripts/compare_models.py > docs/model-comparison.md
"""

from __future__ import annotations

import sys

from minutes_ai.evaluate import Score, evaluate
from minutes_ai.extract import Extractor

SETS = [("data/test", "評価専用（test・6 件）"), ("data/dev", "開発用（dev・6 件）")]
MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    print("# モデル比較\n")
    print(
        "`scripts/compare_models.py` で生成。各モデル 1 回ずつの実行結果。effort はすべて `medium`。"
    )
    print("費用はキャッシュした実際の API 使用量から算出した、会議 100 件あたりの推定額（USD）。\n")
    for directory, title in SETS:
        print(f"## {title}\n")
        print(
            "| モデル | 決定事項 F1 | タスク F1 | 未決事項 F1 | 期限の正解率 | 根拠の実在率 | 100 件あたり費用 |"
        )
        print("|---|---|---|---|---|---|---|")
        for model in MODELS:
            extractor = Extractor(model=model, offline=True)
            try:
                results = evaluate(directory, extractor)
            except LookupError:
                continue  # この組み合わせは未測定
            scores = {"d": Score(), "a": Score(), "o": Score()}
            for r in results:
                scores["d"].add(r.decisions)
                scores["a"].add(r.action_items)
                scores["o"].add(r.open_issues)
            due = sum(r.due_correct for r in results)
            grounded = sum(r.grounded for r in results)
            items = sum(r.items for r in results)
            cost = extractor.usage.estimated_cost_usd / len(results) * 100
            print(
                f"| {model} | {scores['d'].f1:.2f} | {scores['a'].f1:.2f} | {scores['o'].f1:.2f} | "
                f"{due}/{scores['a'].matched} | {grounded}/{items} | ${cost:.2f} |"
            )
        print()


if __name__ == "__main__":
    main()
