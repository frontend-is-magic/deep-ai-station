"""Fixed migration and report CLI boundaries without database access."""

import argparse
import json
from datetime import date

import pytest

from backend.quota import Config
from scripts import report_model_usage, setup_quota


@pytest.mark.parametrize("value", ["20261004", "2026-1-01", "2026-02-30", "private", "9999-12-31"])
def test_report_date_is_strict_and_fixed(value):
    with pytest.raises(argparse.ArgumentTypeError, match="^日期必须为 YYYY-MM-DD$"):
        report_model_usage.report_date(value)


def test_report_main_prints_one_json(monkeypatch, capsys):
    async def report(day):
        assert day == date(2026, 10, 4)
        return {"known_usage": {"total_tokens": 0, "prompt_tokens": None}}

    monkeypatch.setattr(report_model_usage, "report", report)
    assert report_model_usage.main(["--date", "2026-10-04"]) == 0
    captured = capsys.readouterr()
    assert captured.err == "" and len(captured.out.splitlines()) == 1
    assert json.loads(captured.out)["known_usage"] == {"total_tokens": 0, "prompt_tokens": None}


def test_report_main_hides_every_internal_diagnostic(monkeypatch, capsys):
    async def fail(day):
        raise RuntimeError("private-diagnostic")

    monkeypatch.setattr(report_model_usage, "report", fail)
    assert report_model_usage.main(["--date", "2026-10-04"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "模型请求用量汇总失败，请检查托管配置与数据库权限。\n"


async def test_report_refuses_nonpersistent_store(monkeypatch):
    monkeypatch.setattr(report_model_usage, "configuration", lambda: Config("memory", "local", ""))
    with pytest.raises(ValueError, match="PostgreSQL is required"):
        await report_model_usage.report(date(2026, 10, 4))


async def test_setup_applies_two_fixed_migrations_in_one_transaction(monkeypatch):
    class Connection:
        closed = False

        def __init__(self):
            self.sql = []
            self.commits = 0

        async def execute(self, sql):
            self.sql.append(sql)

        async def commit(self):
            self.commits += 1

        async def close(self):
            self.closed = True

    connection = Connection()

    async def connect(*args, **kwargs):
        return connection

    monkeypatch.setattr(
        setup_quota,
        "configuration",
        lambda: Config("postgres", "unit", "postgresql://invalid/unit"),
    )
    monkeypatch.setattr(setup_quota.QuotaConnection, "connect", connect)
    await setup_quota.setup()
    assert connection.closed and connection.commits == 1 and len(connection.sql) == 2
    sql = "\n".join(connection.sql)
    assert sql.index("ai_request_quota_v1") < sql.index("ai_model_usage_v1")
    assert "CREATE INDEX IF NOT EXISTS ai_model_usage_v1_scope_admitted_at" in sql
    assert "DROP" not in sql
