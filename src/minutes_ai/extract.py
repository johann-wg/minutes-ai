"""Claude API による抽出.

応答は入力のハッシュをキーにキャッシュする。同じ評価を再実行するときは API を呼ばないため、
CI や第三者は費用ゼロで結果を再現できる。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from minutes_ai import grounding
from minutes_ai.models import OUTPUT_SCHEMA, Minutes

DEFAULT_MODEL = "claude-opus-5-5"
# プロンプトを変えたら上げる. キャッシュキーに含まれるため古い応答は使われなくなる
PROMPT_VERSION = "2"

# 100 万トークンあたりの料金 (USD, 入力 / 出力). Anthropic 公式の API 料金
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}
# 拒否時にサーバー側で別モデルに切り替える機能 (fallbacks) が使えるモデル
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}

LANG_NAMES = {"ja": "日本語", "ko": "韓国語"}

SYSTEM_PROMPT = """\
あなたは会議録・問い合わせメールを整理するアシスタントです。
入力のテキスト (日本語・韓国語・混在のいずれか) から、次の 4 つを抽出してください。

- summary: 全体の要約 (2〜3 文)
- decisions: その場で確定した事項。「〜しないことにした」「見送る」といった決定も含める
- action_items: 担当者が引き受けたタスク
- open_issues: 結論が出ず、持ち越し・保留・未定になった事項

抽出のルール:
- action_items には、担当者が明確に引き受けたものだけを入れる。次は入れない:
  - 途中で取り消された・不要になった・他の人や他社に移ったタスク (最終的な担当者だけを残す)
  - 「いつか〜したい」などの願望や、将来の課題として挙げただけのもの
- 担当者名 (owner) は原文の表記のまま書く (韓国語の名前もハングルのまま)
- summary・text・task は、入力の言語にかかわらず必ず指定された出力言語で書く (韓国語の入力なら翻訳する)
- 期限 (due) は会議日を基準に YYYY-MM-DD に変換する (例: 会議日が火曜なら「明後日」は木曜)。
  会議中に期限が変更された場合は最終的な期限にする。期限が示されていなければ null
- quote には、その項目の根拠となる原文の一部を、翻訳・要約せずにそのままコピーする
- 原文にない情報を補わない
"""


@dataclass
class Usage:
    """API 呼び出しの集計.

    キャッシュから返した分も、実際に API を呼んだ場合の費用として estimated_cost_usd に含める。
    """

    calls: int = 0
    cache_hits: int = 0
    by_model: dict[str, list[int]] = field(default_factory=dict)
    cached_by_model: dict[str, list[int]] = field(default_factory=dict)

    def add(self, model: str, input_tokens: int, output_tokens: int, cached: bool = False) -> None:
        if cached:
            self.cache_hits += 1
            target = self.cached_by_model
        else:
            self.calls += 1
            target = self.by_model
        tokens = target.setdefault(model, [0, 0])
        tokens[0] += input_tokens
        tokens[1] += output_tokens

    @staticmethod
    def _cost(by_model: dict[str, list[int]]) -> float:
        total = 0.0
        for model, (inp, out) in by_model.items():
            pin, pout = PRICES.get(model, (0.0, 0.0))
            total += inp / 1e6 * pin + out / 1e6 * pout
        return total

    @property
    def cost_usd(self) -> float:
        """今回実際に API で使った費用."""
        return self._cost(self.by_model)

    @property
    def estimated_cost_usd(self) -> float:
        """キャッシュがなかった場合の費用."""
        return self.cost_usd + self._cost(self.cached_by_model)


class Extractor:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        effort: str = "medium",
        cache_dir: str | Path = "llm-cache",
        offline: bool = False,
    ):
        self.model = model
        self.effort = effort
        self.cache_dir = Path(cache_dir)
        self.offline = offline
        self.usage = Usage()
        self._client = None

    def _user_message(self, text: str, meeting_date: str, lang: str) -> str:
        return (
            f"会議日 (期限の基準日): {meeting_date}\n"
            f"出力言語: {LANG_NAMES[lang]} (quote は原文のまま)\n\n"
            f"--- 入力 ---\n{text}"
        )

    def _cache_path(self, message: str) -> Path:
        key = hashlib.sha256(
            json.dumps(
                {
                    "model": self.model,
                    "effort": self.effort,
                    "prompt": PROMPT_VERSION,
                    "input": message,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:24]
        return self.cache_dir / self.model / f"{key}.json"

    def _call(self, message: str) -> dict[str, Any]:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 16000,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": message}],
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
            },
        }
        if self.model in FALLBACK_MODELS:
            response = self._client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **params
            )
        else:
            response = self._client.messages.create(**params)

        usage = response.usage
        self.usage.add(response.model, usage.input_tokens, usage.output_tokens)
        record: dict[str, Any] = {
            "model": response.model,
            "stop_reason": response.stop_reason,
            "usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
            "result": None,
        }
        if response.stop_reason not in ("refusal", "max_tokens"):
            text = next(b.text for b in response.content if b.type == "text")
            record["result"] = json.loads(text)
        return record

    def extract(self, text: str, meeting_date: str, lang: str = "ja") -> Minutes:
        message = self._user_message(text, meeting_date, lang)
        path = self._cache_path(message)
        if path.exists():
            record = json.loads(path.read_text(encoding="utf-8"))
            u = record["usage"]
            self.usage.add(self.model, u["input_tokens"], u["output_tokens"], cached=True)
        elif self.offline:
            raise LookupError(f"キャッシュがありません (offline): {path.name}")
        else:
            record = self._call(message)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )

        if record["result"] is None:
            raise RuntimeError(f"抽出できませんでした (stop_reason={record['stop_reason']})")
        return grounding.check(Minutes.from_dict(record["result"]), text)
