"""Apply the fixed quota schema using server-managed environment configuration."""

import asyncio
import sys
from pathlib import Path

# Support `python scripts/setup_quota.py` without installing the application.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.quota import QuotaConnection, configuration  # noqa: E402


async def setup():
    config = configuration()
    if config.mode != "postgres":
        raise ValueError("PostgreSQL is required")
    sql = (
        Path(__file__).resolve().parents[1] / "backend/migrations/001_request_quota.sql"
    ).read_text()
    connection = None
    try:
        async with asyncio.timeout(10):
            connection = await QuotaConnection.connect(
                config.database_url,
                connect_timeout=3,
                options="-c statement_timeout=5000 -c lock_timeout=1000 -c idle_in_transaction_session_timeout=6000",
            )
            await connection.execute(sql)
            await connection.commit()
    finally:
        if connection is not None and not connection.closed:
            await connection.close()


def main() -> int:
    try:
        asyncio.run(setup())
    except (Exception, KeyboardInterrupt):
        print("配额表初始化失败，请检查托管配置与数据库权限。", file=sys.stderr)
        return 1
    print("配额表初始化完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
