"""Print one UTC day's durable known usage; unknown token counts remain null."""

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.quota import _store, configuration  # noqa: E402


def report_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value or parsed == date.max:
            raise ValueError
        return parsed
    except ValueError:
        raise argparse.ArgumentTypeError("日期必须为 YYYY-MM-DD") from None


async def report(day: date):
    config = configuration()
    if config.mode != "postgres":
        raise ValueError("PostgreSQL is required")
    return await _store(config).model_usage_report(day)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, type=report_date, help="UTC 日期 YYYY-MM-DD")
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(report(args.date))
        rendered = json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (Exception, KeyboardInterrupt):
        print("模型请求用量汇总失败，请检查托管配置与数据库权限。", file=sys.stderr)
        return 1
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
