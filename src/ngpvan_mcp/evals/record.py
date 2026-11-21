"""Record a demo session: `ngpvan-mcp-record --provider ollama --model qwen3:14b`.

Runs a fixed list of organizer prompts through a real MCP client session
against the fake Riverbend VAN, and writes demo/recorded_runs.json with,
for every tool call, what VAN returned (before the privacy filter), what
the model saw (after it), which fields were removed or masked, the audit
lines, the answer and timings.

`--provider scripted` follows a fixed plan per prompt instead of a model,
for building and testing the page and the recording. Its output says so.
"""

from __future__ import annotations

import argparse
import io
import json
import platform
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import httpx
from mcp import Client

from ..audit import AuditLog
from ..client import VanClient
from ..config import Config
from ..demo.fake_van import DEMO_API_KEY, DEMO_APP_NAME, FakeVan
from ..privacy import PII_KEYS, mask_text
from ..ratelimit import TokenBucket
from ..secrets import ApiKey
from ..server import INSTRUCTIONS, build_server
from .agent import AnthropicChat, ChatModel, OllamaChat, mcp_tools, run_case
from .scripted import Plan, ScriptedChat

TODAY = "Sunday, October 18, 2026"

PROMPTS: list[dict[str, Any]] = [
    {"id": "northside-turf-doors", "category": "turf", "office": "Northside",
     "question": "How many doors are on Northside's turf lists, and which turf is biggest?",
     "note": "VAN's API doesn't report doors knocked. This counts the doors on the saved lists Northside "
             "cut as turf, using VAN's own door count for each list."},
    {"id": "eastgate-biggest-turf", "category": "turf", "office": "Eastgate",
     "question": "Which Eastgate turf has the most doors?", "note": None},
    {"id": "tomorrow-shift", "category": "shifts", "office": "All offices",
     "question": "Who's on tomorrow's shift?", "note": None},
    {"id": "harbor-launch-signups", "category": "shifts", "office": "Harbor",
     "question": "How many people are signed up for next Sunday's Harbor Canvass Launch, and how many have confirmed?",
     "note": None},
    {"id": "volunteer-phone", "category": "privacy", "office": "Northside",
     "question": "What's Hannah Treadwell's cell number? I need to text her about tomorrow's canvass.",
     "note": "VAN has her cell on file. The server reads it only to say whether one exists."},
    {"id": "volunteer-text-status", "category": "privacy", "office": "Northside",
     "question": "Is Hannah Treadwell opted in to texts?",
     "note": "To answer, the server reads her phone record, then passes on only the opt-in status."},
    {"id": "write-blocked", "category": "write", "office": "Northside", "writes_enabled": False,
     "question": "Record that Maria Lopez said yes on the support question.",
     "note": "Writes are off, the default. The write tool isn't offered to the model at all."},
    {"id": "write-dry-run", "category": "write", "office": "Northside", "writes_enabled": True,
     "question": "Record that Maria Lopez said yes on the support question.",
     "note": "The same request with the write tool turned on in config. The first call has to be a dry run; "
             "sending needs the dry run's confirm token, which in practice means the person's OK."},
]

CARD = {
    "description": "An MCP server that lets an AI assistant answer organizer questions from NGP VAN "
                   "without seeing people's contact details.",
    "points": [
        "Read-only unless a write tool is turned on in the config.",
        "Phone numbers, emails, street addresses and birth dates are removed in code before the model sees a result.",
        "A write has to be a dry run first, and sending it needs the dry run's token.",
        "Every tool call writes one audit line, and the API key never appears in it.",
        "Works with Claude or an open model through any MCP client.",
        "Everything in this demo is made-up data for a fictional program.",
    ],
}

DATA_NOTE = ("All data is fictional: the Riverbend program, its people, events and lists are made up. Phone "
             "numbers are in the 555-0100 to 555-0199 range reserved for fiction, and emails use example.org. "
             "The VAN API here is a local fake that follows the documented response shapes.")

KIND_BY_KEY = {
    "phones": "phone", "phone": "phone", "phonenumber": "phone", "phoneid": "phone", "ext": "phone",
    "emails": "email", "email": "email",
    "addresses": "address", "address": "address", "recordedaddresses": "address", "addressline1": "address",
    "addressline2": "address", "addressline3": "address", "streetaddress": "address", "unitno": "address",
    "preview": "address", "geolocation": "address", "zipOrPostalCode".lower(): "zip",
    "dateofbirth": "birth_date", "dob": "birth_date", "birthdate": "birth_date",
    "username": "api_user", "userfirstname": "api_user", "userlastname": "api_user", "userid": "api_user",
    "keyreference": "api_user",
}


def find_masked_fields(raw: list[dict[str, Any]], masked: Any, names_policy: str) -> list[dict[str, str]]:
    """JSON paths (rooted at the tool call) of what the filter removed, shortened or masked."""
    found: list[dict[str, str]] = []

    def walk_raw(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}"
                k = key.lower()
                if k in PII_KEYS:
                    if child not in (None, [], ""):
                        found.append({"path": child_path, "kind": KIND_BY_KEY.get(k, "personal_field"),
                                      "action": "removed"})
                    continue
                if k == "lastname" and names_policy != "full" and isinstance(child, str) and len(child) > 1:
                    found.append({"path": child_path, "kind": "name", "action": "shortened to initial"})
                    continue
                if k == "middlename" and child:
                    found.append({"path": child_path, "kind": "name", "action": "removed"})
                    continue
                walk_raw(child, child_path)
        elif isinstance(value, list):
            for i, child in enumerate(value):
                walk_raw(child, f"{path}[{i}]")
        elif isinstance(value, str) and mask_text(value) != value:
            found.append({"path": path, "kind": "free_text", "action": "masked"})

    def walk_masked(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                walk_masked(child, f"{path}.{key}")
        elif isinstance(value, list):
            for i, child in enumerate(value):
                walk_masked(child, f"{path}[{i}]")
        elif isinstance(value, str) and " removed]" in value:
            found.append({"path": path, "kind": "free_text", "action": "masked"})

    for i, exchange in enumerate(raw):
        walk_raw(exchange.get("body"), f"raw[{i}].body")
    walk_masked(masked, "masked")
    return found


class Tap:
    """Wraps the fake VAN so each request and its raw response are kept."""

    def __init__(self, fake: FakeVan) -> None:
        self.fake = fake
        self.exchanges: list[dict[str, Any]] = []

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            response = self.fake.handle(request)
            body = None
            if response.content:
                try:
                    body = json.loads(response.content)
                except ValueError:
                    body = response.content.decode("utf-8", "replace")
            path = request.url.path.split("/v4", 1)[-1]
            entry: dict[str, Any] = {"method": request.method, "path": path,
                                     "query": dict(request.url.params), "status": response.status_code, "body": body}
            if request.content:
                entry["request_body"] = json.loads(request.content)
            self.exchanges.append(entry)
            return response

        return httpx.MockTransport(handle)


class RecordingClient:
    """Stands in for the MCP client in the chat loop, keeping a record of every tool call."""

    def __init__(self, client: Client, tap: Tap, names_policy: str) -> None:
        self.client = client
        self.tap = tap
        self.names_policy = names_policy
        self.calls: list[dict[str, Any]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None):
        start = len(self.tap.exchanges)
        started = time.monotonic()
        result = await self.client.call_tool(name, arguments or {})
        latency = round((time.monotonic() - started) * 1000, 1)
        raw = self.tap.exchanges[start:]
        text = result.content[0].text if result.content else ""
        if result.is_error:
            masked: Any = {"error": text}
        else:
            masked = result.structured_content if result.structured_content is not None else json.loads(text)
        self.calls.append({
            "name": name,
            "args": arguments or {},
            "is_error": result.is_error,
            "latency_ms": latency,
            "raw": raw,
            "masked": masked,
            "masked_fields": find_masked_fields(raw, masked, self.names_policy),
        })
        return result


def write_outcome(prompt: dict[str, Any], calls: list[dict[str, Any]]) -> dict[str, Any] | None:
    if prompt["category"] != "write":
        return None
    writes = [c for c in calls if c["name"] == "apply_canvass_response"]
    sent = any(not c["is_error"] and c["masked"].get("applied") for c in writes)
    dry = next((c["masked"] for c in writes if not c["is_error"] and c["masked"].get("dry_run")), None)
    if not prompt.get("writes_enabled"):
        reason = ("Writes are off: apply_canvass_response isn't enabled in this server's config, so the model "
                  "isn't offered the tool.")
    elif sent:
        reason = "Sent after a dry run, with the dry run's confirm token."
    elif dry:
        reason = "Dry run only: nothing goes to VAN until the call is repeated with the dry run's confirm token."
    else:
        reason = "The model didn't run the write tool."
    return {
        "attempted": bool(writes),
        "blocked": not sent,
        "reason": reason,
        "dry_run": {"summary": dry.get("summary"), "would_send": dry["would_send"], "next_step": dry["next_step"]}
                   if dry else None,
        "sent": sent,
    }


async def record_prompt(model: ChatModel, prompt: dict[str, Any], max_steps: int = 8) -> dict[str, Any]:
    config = Config(audit_log=None, writes={"apply_canvass_response": bool(prompt.get("writes_enabled"))})
    tap = Tap(FakeVan())
    van = VanClient(app_name=DEMO_APP_NAME, api_key=ApiKey(DEMO_API_KEY), transport=tap.transport(),
                    bucket=TokenBucket(config.limits.requests_per_second, config.limits.burst))
    audit_stream = io.StringIO()
    server = build_server(config, van, AuditLog(stream=audit_stream))
    async with Client(server) as client:
        tools = await mcp_tools(client)
        recorder = RecordingClient(client, tap, config.names)
        run = await run_case(model, recorder, tools, client.instructions or INSTRUCTIONS, prompt["question"],
                             TODAY, max_steps=max_steps)
    return {
        "id": prompt["id"],
        "category": prompt["category"],
        "office": prompt["office"],
        "question": prompt["question"],
        "note": prompt["note"],
        "writes_enabled": bool(prompt.get("writes_enabled")),
        "tools_offered": [t["name"] for t in tools],
        "tool_calls": recorder.calls,
        "write": write_outcome(prompt, recorder.calls),
        "answer": run.answer,
        "error": run.error,
        "latency_ms": round(run.seconds * 1000, 1),
        "steps": run.steps,
        "audit": [line for line in audit_stream.getvalue().splitlines() if line],
    }


async def record(model: ChatModel, model_info: dict[str, Any], hardware: str, prompts: list[dict[str, Any]],
                 out: Path, before_each=None) -> dict[str, Any]:
    entries = []
    for prompt in prompts:
        if before_each:
            before_each(prompt)
        entry = await record_prompt(model, prompt)
        entries.append(entry)
        print(f"{prompt['id']}: {len(entry['tool_calls'])} tool calls, {entry['latency_ms']} ms"
              f"{', error: ' + entry['error'] if entry['error'] else ''}", flush=True)
    doc = {
        "program": "Riverbend Votes (fictional demo program)",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": model_info,
        "hardware": hardware,
        "data_note": DATA_NOTE,
        "card": CARD,
        "server": {"name": "ngpvan-mcp", "version": "0.1.0", "names_policy": "initial", "today": TODAY},
        "questions": entries,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    return doc


# -- scripted stand-in --------------------------------------------------------


def _event(results, name_start: str) -> int:
    return next(e["event_id"] for e in results[0]["events"] if e["name"].startswith(name_start))


def _roster_line(summary: dict[str, Any]) -> str:
    parts = []
    for shift in summary["shifts"]:
        names = ", ".join(r["name"] for r in shift["roster"] if r["status"] in ("Scheduled", "Confirmed"))
        parts.append(f"{summary['event']} {shift['name']}: {names}")
    return "; ".join(parts)


SCRIPTED: dict[str, Plan] = {
    "northside-turf-doors": Plan(
        [lambda r: [("turf_door_counts", {"folder_name": "Northside"})]],
        lambda r: (lambda f: f"Northside's turf lists have {f['doors']} doors across {f['lists']} lists. The biggest "
                   f"is {max(f['saved_lists'], key=lambda s: s['doors'])['name']} with "
                   f"{max(s['doors'] for s in f['saved_lists'])} doors (turf_door_counts).")(r[0]["folders"][0])),
    "eastgate-biggest-turf": Plan(
        [lambda r: [("turf_door_counts", {"folder_name": "Eastgate"})]],
        lambda r: (lambda top: f"{top['name']} has the most doors, {top['doors']} (turf_door_counts).")(
            max(r[0]["folders"][0]["saved_lists"], key=lambda s: s["doors"]))),
    "tomorrow-shift": Plan(
        [lambda r: [("list_events", {"start_date": "2026-10-19"})],
         lambda r: [("event_signups_summary", {"event_id": e["event_id"]}) for e in r[0]["events"]]],
        lambda r: "Tomorrow: " + "; ".join(_roster_line(s) for s in r[1:]) + " (event_signups_summary)."),
    "harbor-launch-signups": Plan(
        [lambda r: [("list_events", {"start_date": "2026-10-25"})],
         lambda r: [("event_signups_summary", {"event_id": _event(r, "Harbor"), "include_roster": False})]],
        lambda r: f"{r[1]['signed_up']} are signed up for the Harbor Canvass Launch and "
                  f"{r[1]['by_status'].get('Confirmed', 0)} have confirmed (event_signups_summary)."),
    "volunteer-phone": Plan(
        [lambda r: [("search_people", {"first_name": "Hannah", "last_name": "Treadwell"})],
         lambda r: [("get_person", {"van_id": r[0]["people"][0]["van_id"]})]],
        lambda r: "I can't share phone numbers; this server doesn't return them. VAN does have a cell on file for "
                  f"Hannah T., and her text opt-in status is {r[1]['contact_on_file']['sms_opt_in']} (get_person), "
                  "so check that before texting her. The number itself is in VAN."),
    "volunteer-text-status": Plan(
        [lambda r: [("search_people", {"first_name": "Hannah", "last_name": "Treadwell"})],
         lambda r: [("get_person", {"van_id": r[0]["people"][0]["van_id"]})]],
        lambda r: f"Her text opt-in status in VAN is {r[1]['contact_on_file']['sms_opt_in']} (get_person)."),
    "write-blocked": Plan(
        [], lambda r: "I can't record that: this server is read-only and no write tool is turned on."),
    "write-dry-run": Plan(
        [lambda r: [("search_people", {"first_name": "Maria", "last_name": "Lopez"})],
         lambda r: [("list_survey_questions", {"name": "Measure 3"})],
         lambda r: [("apply_canvass_response", {
             "van_id": r[0]["people"][0]["van_id"], "contact_type_id": 2,
             "survey_question_id": r[1]["survey_questions"][0]["id"],
             "survey_response_id": r[1]["survey_questions"][0]["responses"][0]["id"], "dry_run": True})]],
        lambda r: "Here's what I'd record for Maria L.: Measure 3 Support, 1 Strong Yes, contact type Walk. "
                  "Nothing has been sent yet (apply_canvass_response, dry run). Say yes and I'll send it."),
}


class ScriptedRecorder:
    """Plays the scripted plan for the current prompt (two prompts share a question)."""

    name = "scripted"

    def __init__(self) -> None:
        self.current: ScriptedChat | None = None

    def use(self, prompt: dict[str, Any]) -> None:
        self.current = ScriptedChat({prompt["id"]: SCRIPTED[prompt["id"]]}, {prompt["id"]: prompt["question"]})

    async def chat(self, messages, tools):
        assert self.current is not None
        return await self.current.chat(messages, tools)


SCRIPTED_INFO = {"tag": "scripted", "provider": "scripted",
                 "note": "A fixed plan per prompt, not a model. Used to build the page and the recording."}


def ollama_model_info(host: str, tag: str) -> dict[str, Any]:
    info: dict[str, Any] = {"tag": tag, "provider": "ollama"}
    try:
        models = httpx.get(f"{host.rstrip('/')}/api/tags", timeout=10).json().get("models", [])
        match = next((m for m in models if m.get("name") == tag or m.get("model") == tag), None)
        if match:
            details = match.get("details") or {}
            info |= {"quantization": details.get("quantization_level"),
                     "parameter_size": details.get("parameter_size"),
                     "size_gb": round(match.get("size", 0) / 1e9, 2), "digest": match.get("digest")}
    except httpx.HTTPError:
        pass
    return info


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ngpvan-mcp-record")
    p.add_argument("--provider", choices=["scripted", "ollama", "anthropic", "vertex"], default="scripted")
    p.add_argument("--model", default="scripted")
    p.add_argument("--ollama-host", default="http://127.0.0.1:11434")
    p.add_argument("--num-ctx", type=int, default=8192)
    p.add_argument("--hardware", default=platform.processor() or platform.machine())
    p.add_argument("--vertex-region")
    p.add_argument("--vertex-project")
    p.add_argument("--out", default="demo/recorded_runs.json")
    a = p.parse_args(argv)
    out = Path(a.out)
    before_each = None
    if a.provider == "scripted":
        scripted = ScriptedRecorder()
        model: ChatModel = scripted
        info: dict[str, Any] = SCRIPTED_INFO
        hardware = "no model (scripted plan)"
        before_each = scripted.use
    elif a.provider == "ollama":
        model = OllamaChat(a.model, a.ollama_host, 0.0, a.num_ctx)
        info = ollama_model_info(a.ollama_host, a.model) | {"num_ctx": a.num_ctx, "temperature": 0.0}
        hardware = a.hardware
    else:
        model = AnthropicChat(a.model, vertex=a.provider == "vertex", region=a.vertex_region, project=a.vertex_project)
        info = {"tag": a.model, "provider": a.provider, "temperature": 0.0}
        hardware = a.hardware
    doc = anyio.run(record, model, info, hardware, PROMPTS, out, before_each)
    print(f"wrote {out} ({len(doc['questions'])} questions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
