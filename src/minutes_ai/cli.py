"""コマンドラインインターフェース."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from minutes_ai.extract import DEFAULT_MODEL, Extractor, Usage


def _extractor(args: argparse.Namespace) -> Extractor:
    return Extractor(
        model=args.model, effort=args.effort, cache_dir=args.cache_dir, offline=args.offline
    )


def _print_usage(usage: Usage) -> None:
    print(
        f"API 呼び出し {usage.calls} 回 / キャッシュ利用 {usage.cache_hits} 回 / "
        f"費用 約 ${usage.cost_usd:.4f} (キャッシュなしの場合 約 ${usage.estimated_cost_usd:.4f})",
        file=sys.stderr,
    )


def _cmd_extract(args: argparse.Namespace) -> int:
    from minutes_ai import export

    path = Path(args.input)
    extractor = _extractor(args)
    minutes = extractor.extract(path.read_text(encoding="utf-8"), args.date, args.lang)
    if args.json:
        print(json.dumps(minutes.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(export.to_markdown(minutes, title=path.stem))
    if args.xlsx:
        export.to_excel(minutes, args.xlsx)
        print(f"saved: {args.xlsx}", file=sys.stderr)
    if args.md:
        Path(args.md).write_text(export.to_markdown(minutes, title=path.stem), encoding="utf-8")
        print(f"saved: {args.md}", file=sys.stderr)
    _print_usage(extractor.usage)
    return 0


def _cmd_eval(args: argparse.Namespace) -> int:
    from minutes_ai.evaluate import evaluate, summarize

    extractor = _extractor(args)
    table = summarize(evaluate(args.data, extractor))
    print(table)
    if args.report:
        Path(args.report).write_text(table + "\n", encoding="utf-8")
    _print_usage(extractor.usage)
    return 0


def _add_llm_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--model", default=DEFAULT_MODEL, help="Claude のモデル ID")
    p.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--cache-dir", default="llm-cache", help="API 応答のキャッシュ先")
    p.add_argument(
        "--offline", action="store_true", help="API を呼ばずキャッシュのみ使う (CI・再現用)"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="minutes-ai", description="会議録・問い合わせから決定事項・タスク・未決事項を抽出する"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("extract", help="テキストファイルから抽出する")
    p.add_argument("input")
    p.add_argument(
        "--date", default=dt.date.today().isoformat(), help="会議日 (期限の基準日, YYYY-MM-DD)"
    )
    p.add_argument("--lang", default="ja", choices=["ja", "ko"], help="出力言語")
    p.add_argument("--xlsx", help="タスク管理表 (Excel) の保存先")
    p.add_argument("--md", help="議事録 (Markdown) の保存先")
    p.add_argument("--json", action="store_true", help="JSON で出力")
    _add_llm_args(p)
    p.set_defaults(func=_cmd_extract)

    p = sub.add_parser("eval", help="正解データで抽出精度を評価する")
    p.add_argument("data", nargs="?", default="data/dev")
    p.add_argument("--report", help="結果を Markdown で保存するパス")
    _add_llm_args(p)
    p.set_defaults(func=_cmd_eval)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
