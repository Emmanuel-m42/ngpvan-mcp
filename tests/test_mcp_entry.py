"""Through the real MCP entry points: an in-process client, the stdio command and the HTTP app."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
import pytest
from mcp import Client, StdioServerParameters

from ngpvan_mcp.cli import BearerAuth
from ngpvan_mcp.config import Config, Limits
from ngpvan_mcp.server import _cap

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_TOOLS = {
    "check_connection", "search_people", "get_person", "person_activist_codes", "person_signups",
    "contact_history_summary", "list_events", "event_signups_summary", "list_folders", "turf_door_counts",
    "list_survey_questions", "list_activist_codes", "canvass_codes",
}


async def test_lists_the_tools_with_schemas(server_factory):
    async with Client(server_factory()) as c:
        tools = {tool.name: tool for tool in (await c.list_tools()).tools}
    assert set(tools) == EXPECTED_TOOLS
    assert tools["list_events"].input_schema["required"] == ["start_date"]
    assert "phone" not in json.dumps(tools["search_people"].input_schema["properties"]).lower()


async def test_call_returns_structured_and_text_content(server_factory):
    async with Client(server_factory()) as c:
        result = await c.call_tool("turf_door_counts", {"folder_name": "Northside"})
    assert not result.is_error
    data = result.structured_content
    assert data["folders"][0]["folder"] == "Northside Turfs"
    assert json.loads(result.content[0].text) == data


async def test_bad_arguments_are_rejected_by_the_schema(server_factory):
    async with Client(server_factory()) as c:
        result = await c.call_tool("get_person", {"van_id": -3})
    assert result.is_error


async def test_van_errors_come_back_as_tool_errors(server_factory):
    async with Client(server_factory()) as c:
        result = await c.call_tool("get_person", {"van_id": 42})
    assert result.is_error
    assert "no record with that id" in result.content[0].text


async def test_rate_limited_van_is_retried_transparently(demo_data, audit_path):
    from ngpvan_mcp.audit import AuditLog
    from ngpvan_mcp.demo.fake_van import FakeVan
    from ngpvan_mcp.server import build_server

    from .conftest import make_van, read_audit

    fake = FakeVan(demo_data, rate_limit_every=2)
    server = build_server(Config(audit_log=None), make_van(fake), AuditLog(audit_path))
    async with Client(server) as c:
        result = await c.call_tool("turf_door_counts", {"folder_name": "Harbor"})
    assert not result.is_error
    assert read_audit(audit_path)[0]["retries"] >= 1


def test_cap_trims_lists_and_flags_it():
    big = {"rows": list(range(100)), "nested": {"more": [{"x": "y" * 50}] * 30}}
    out, truncated = _cap(big, max_items=10, max_chars=10_000)
    assert truncated and out["truncated"] is True
    assert len(out["rows"]) == 10 and len(out["nested"]["more"]) == 10
    out, truncated = _cap({"rows": ["z" * 100] * 10}, max_items=25, max_chars=400)
    assert truncated and len(json.dumps(out)) < 900


async def test_small_result_cap_through_the_server(server_factory):
    cfg = Config(audit_log=None, limits=Limits(max_items=2))
    async with Client(server_factory(cfg)) as c:
        result = await c.call_tool("turf_door_counts", {"folder_name": "Northside"})
    data = result.structured_content
    assert data["truncated"] is True
    assert len(data["folders"][0]["saved_lists"]) == 2
    # Totals are computed before trimming, so they still cover every list.
    assert data["folders"][0]["lists"] == 5


async def test_stdio_command_end_to_end(tmp_path):
    """Launch `ngpvan-mcp --demo` as a subprocess, the way Claude Desktop does."""
    config = tmp_path / "cfg.toml"
    config.write_text(f'[audit]\npath = "{tmp_path / "audit.jsonl"}"\n')
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "ngpvan_mcp.cli", "--demo", "--config", str(config)],
        env={**os.environ, "VAN_API_KEY": ""},
        cwd=str(tmp_path),
    )
    async with Client(params) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        result = await c.call_tool("check_connection", {})
    assert names == EXPECTED_TOOLS
    assert result.structured_content["committee"] == "Riverbend Votes (demo)"
    assert (tmp_path / "audit.jsonl").read_text().count("\n") == 1


async def _asgi_echo(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


@pytest.mark.parametrize("header,status", [(None, 401), ("Bearer wrong", 401), ("Bearer s3cret", 200)])
async def test_http_transport_requires_the_bearer_token(header, status):
    app = BearerAuth(_asgi_echo, "s3cret")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        headers = {"authorization": header} if header else {}
        resp = await http.post("/mcp", headers=headers)
    assert resp.status_code == status


def test_http_refuses_to_start_without_a_token(monkeypatch, capsys):
    from ngpvan_mcp.cli import main

    monkeypatch.delenv("NGPVAN_MCP_HTTP_TOKEN", raising=False)
    assert main(["--demo", "--transport", "http"]) == 2
    assert "NGPVAN_MCP_HTTP_TOKEN" in capsys.readouterr().err
