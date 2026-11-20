from __future__ import annotations

import base64

from mcp import Client

from ngpvan_mcp.config import Config
from ngpvan_mcp.demo.fake_van import DEMO_API_KEY, DEMO_APP_NAME

from .conftest import read_audit


async def test_one_line_per_call_with_timing_and_request_count(server_factory, audit_path):
    async with Client(server_factory()) as c:
        await c.call_tool("turf_door_counts", {"folder_name": "Harbor"})
        await c.call_tool("list_folders", {})
    lines = read_audit(audit_path)
    assert [line["tool"] for line in lines] == ["turf_door_counts", "list_folders"]
    first = lines[0]
    assert first["status"] == "ok"
    assert first["van_requests"] == 2
    assert first["duration_ms"] >= 0
    assert first["args"] == {"folder_id": None, "folder_name": "Harbor"}
    assert {"ts", "tool", "args", "status", "duration_ms", "van_requests", "retries"} <= set(first)


async def test_names_in_arguments_are_redacted(server_factory, audit_path):
    async with Client(server_factory()) as c:
        await c.call_tool("search_people", {"first_name": "Maria", "city": "Riverbend"})
    entry = read_audit(audit_path)[0]
    assert entry["args"]["first_name"] == "[redacted, 5 chars]"
    assert entry["args"]["city"] == "[redacted, 9 chars]"
    assert "Maria" not in audit_path.read_text()


async def test_errors_and_denials_are_logged_with_a_kind(server_factory, audit_path):
    async with Client(server_factory()) as c:
        await c.call_tool("get_person", {"van_id": 5})
        await c.call_tool("list_events", {"start_date": "next saturday"})
    statuses = [(line["status"], line.get("error_kind")) for line in read_audit(audit_path)]
    assert statuses == [("error", "not_found"), ("error", "bad_input")]


async def test_denied_write_is_logged(van, audit_path):
    from ngpvan_mcp import tools as t
    from ngpvan_mcp.audit import AuditLog
    from ngpvan_mcp.server import Guard

    guard = Guard(t.Toolbox(van=van, config=Config(audit_log=None)), AuditLog(audit_path))
    try:
        await guard("apply_canvass_response", {"van_id": 1, "contact_type_id": 2, "result_code_id": 1,
                                               "dry_run": False}, t.apply_canvass_response)
    except Exception:
        pass
    assert read_audit(audit_path)[0]["status"] == "denied"


async def test_the_api_key_never_reaches_the_log(server_factory, audit_path):
    async with Client(server_factory()) as c:
        for name, args in [("check_connection", {}), ("search_people", {"last_name": "G"}),
                           ("get_person", {"van_id": 100200101}), ("get_person", {"van_id": 1})]:
            await c.call_tool(name, args)
    text = audit_path.read_text()
    secret_forms = [
        DEMO_API_KEY,
        DEMO_API_KEY.split("|")[0],
        base64.b64encode(f"{DEMO_APP_NAME}:{DEMO_API_KEY}".encode()).decode(),
    ]
    assert not [s for s in secret_forms if s in text]
    assert "authorization" not in text.lower()


async def test_results_are_not_copied_into_the_log(server_factory, audit_path, demo_data):
    async with Client(server_factory()) as c:
        await c.call_tool("event_signups_summary", {"event_id": 3001})
    text = audit_path.read_text()
    roster = [s["person"] for s in demo_data["signups"] if s["event"]["eventId"] == 3001]
    assert roster
    assert not [p for p in roster if str(p["vanId"]) in text or p["lastName"] in text]
