"""根拠 (quote) が原文に実在するかの検証.

LLM が原文にない内容を作り出していないかを、プログラムで機械的に確認する。
"""

from __future__ import annotations

import re
import unicodedata

from minutes_ai.models import Minutes

# 引用の前後や途中に付きがちな記号. 比較時は無視する
_IGNORED = re.compile(r"[\s「」『』\"'“”‘’・…]+")


def normalize(text: str) -> str:
    # NFKC で全角英数字・記号を半角にそろえる
    return _IGNORED.sub("", unicodedata.normalize("NFKC", text))


def is_grounded(quote: str, source: str) -> bool:
    q = normalize(quote)
    return bool(q) and q in normalize(source)


def check(minutes: Minutes, source: str) -> Minutes:
    """各項目の grounded を設定して返す."""
    for item in minutes.all_items():
        item.grounded = is_grounded(item.quote, source)
    return minutes
