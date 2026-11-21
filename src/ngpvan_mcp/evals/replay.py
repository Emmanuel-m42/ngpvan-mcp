"""Replay a recorded demo session in the terminal: `ngpvan-mcp-replay demo/recorded_runs.json`.

Shows each question, the tool calls the model made, what VAN returned next
to what the model saw (removed fields highlighted), the write gate, and
the audit log. Nothing is re-run: everything comes from the recording, so
the output is the same every time. This is what docs/demo.gif is made of.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

DEFAULT_IDS = ["northside-turf-doors", "tomorrow-shift", "volunteer-phone", "volunteer-text-status",
               "write-blocked", "write-dry-run"]
SHOWN_RAW_KEYS = ["firstName", "middleName", "lastName", "dateOfBirth", "phones", "emails", "addresses"]


def _flatten(value: Any, path: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        out = []
        for k, v in value.items():
            out.extend(_flatten(v, f"{path}.{k}" if path else k))
        return out
    if isinstance(value, list):
        out = []
        for i, v in enumerate(value):
            out.extend(_flatten(v, f"{path}[{i}]"))
        return out
    return [(path, value)]


def _person_rows(call: dict[str, Any]) -> list[tuple[str, str, bool]]:
    """Key fields of the raw person record, flagged when the filter removed or shortened them."""
    body = call["raw"][0]["body"]
    flagged = {f["path"].removeprefix("raw[0].body.") for f in call["masked_fields"] if f["path"].startswith("raw[0]")}
    rows = []
    for key in SHOWN_RAW_KEYS:
        value = body.get(key)
        if value in (None, []):
            continue
        hit = any(key == f or f.startswith(key + ".") or f.startswith(key + "[") for f in flagged)
        if isinstance(value, list):
            leaf, label = {"phones": ("phoneNumber", "phone"), "emails": ("email", "email"),
                           "addresses": ("addressLine1", "address")}[key]
            for item in value:
                extra = f" ({item['smsOptInStatus']} for texts)" if key == "phones" and "smsOptInStatus" in item else ""
                rows.append((label, f"{item.get(leaf)}{extra}", hit))
        else:
            rows.append((key, str(value)[:10] if key == "dateOfBirth" else str(value), hit))
    return rows


def _masked_rows(masked: dict[str, Any]) -> list[tuple[str, str]]:
    skip = ("source", "note")
    return [(p, json.dumps(v) if not isinstance(v, str) else v) for p, v in _flatten(masked)
            if not p.startswith(skip)]


def _args(args: dict[str, Any]) -> str:
    return ", ".join(f"{k}={json.dumps(v)}" for k, v in args.items() if v is not None)


class Player:
    def __init__(self, console: Console, speed: float) -> None:
        self.c = console
        self.speed = speed

    def pause(self, seconds: float) -> None:
        if self.speed > 0:
            time.sleep(seconds / self.speed)

    def type(self, text: str, style: str = "bold") -> None:
        if self.speed <= 0:
            self.c.print(Text(text, style=style))
            return
        for ch in text:
            self.c.print(Text(ch, style=style), end="")
            time.sleep(0.018 / self.speed)
        self.c.print()

    def header(self, doc: dict[str, Any]) -> None:
        model = doc["model"]
        tag = model.get("tag", "?")
        detail = ", ".join(str(x) for x in (model.get("quantization"), model.get("parameter_size")) if x)
        provider = model.get("provider", "?")
        how = ("a scripted plan, not a model (layout check)" if provider == "scripted"
               else f"a real MCP client session, model served by {provider}")
        settings = ", ".join(f"{k} {model[k]}" for k in ("thinking", "reasoning_effort") if model.get(k))
        name = model.get("label") or tag
        lines = Text()
        lines.append("ngpvan-mcp", style="bold cyan")
        lines.append("  terminal replay of a recorded session\n")
        lines.append(f"Model: {name}{f' ({detail})' if detail else ''}{f' [{tag}]' if name != tag else ''}"
                     f"{f', {settings}' if settings else ''}   Served by: {doc['hardware']}\n")
        lines.append(f"Recorded with ngpvan-mcp-record: {how}. Shown with ngpvan-mcp-replay.\n", style="dim")
        lines.append(f"Data: {doc['program']}, fake VAN API. Today is {doc['server']['today']}.", style="dim")
        self.c.print(Panel(lines, border_style="cyan"))
        self.pause(2.5)

    def question(self, q: dict[str, Any]) -> None:
        self.c.print(Rule(f"[dim]{q['office']}[/dim]", style="grey35"))
        self.c.print(Text("organizer > ", style="bold green"), end="")
        self.type(q["question"])
        if q.get("note"):
            self.c.print(Text(q["note"], style="dim italic"))
        self.pause(0.8)
        if q["category"] == "write" and not q["writes_enabled"]:
            self.c.print(Text("  write tools offered to the model: none (writes are off in config)", style="yellow"))
            self.pause(1.0)
        write = q.get("write")
        follow = q.get("followup")
        self.calls(q["tool_calls"], write)
        if write and write.get("blocked") and not write.get("attempted") and not follow:
            self.c.print(Panel(Text(write["reason"]), title="write blocked", border_style="red", expand=False))
            self.pause(1.5)
        self.answer(q)
        if follow:
            self.c.print(Text("organizer > ", style="bold green"), end="")
            self.type(follow["question"])
            self.pause(0.8)
            self.calls(follow["tool_calls"], write)
            self.answer(follow)

    def calls(self, calls: list[dict[str, Any]], write: dict[str, Any] | None) -> None:
        for call in calls:
            status = "error" if call["is_error"] else f"{len(call['raw'])} VAN request(s), {call['latency_ms']:.0f} ms"
            self.c.print(Text.assemble(("  tool ", "magenta"), (call["name"], "bold magenta"),
                                       (f"({_args(call['args'])})", "magenta"), (f"  {status}", "dim")))
            self.pause(0.6)
            if call["is_error"]:
                message = call["masked"]["error"].removeprefix(f"Error executing tool {call['name']}: ")
                self.c.print(Text(f"    refused by the server: {message}", style="red"))
                self.pause(1.5)
            elif call["name"] == "get_person":
                self.side_by_side(call)
            elif call["name"] == "apply_canvass_response" and call["masked"].get("dry_run") and write:
                self.dry_run(write)

    def answer(self, turn: dict[str, Any]) -> None:
        answer = turn["answer"] or f"(no answer: {turn.get('error')})"
        self.c.print(Text("assistant > ", style="bold blue"), end="")
        self.type(answer, style="")
        self.c.print(Text(f"  {turn['latency_ms'] / 1000:.1f} s for the whole answer", style="dim"))
        self.pause(2.0)

    def side_by_side(self, call: dict[str, Any]) -> None:
        raw = Table.grid(padding=(0, 1))
        for key, value, hit in _person_rows(call):
            raw.add_row(Text(key, style="dim"), Text(value, style="bold red strike" if hit else ""))
        seen = Table.grid(padding=(0, 1))
        for key, value in _masked_rows(call["masked"]):
            seen.add_row(Text(key, style="dim"), Text(value, style="green"))
        pair = Table.grid(expand=True, padding=(0, 1))
        pair.add_column(ratio=1)
        pair.add_column(ratio=1)
        pair.add_row(
            Panel(raw, title="what VAN returned", subtitle="red = removed or shortened", border_style="red"),
            Panel(seen, title="what the model saw", border_style="green"),
        )
        self.c.print(pair)
        self.pause(5.0)

    def dry_run(self, write: dict[str, Any]) -> None:
        send = write["dry_run"]["would_send"]
        body = send["body"]
        summary = write["dry_run"].get("summary") or {}
        lines = [Text(", ".join(f"{k}: {v}" for k, v in summary.items()), style="bold")] if summary else []
        lines.append(Text(f"{send['method']} {send['path']}", style="bold"))
        lines += [Text(f"  {k}: {json.dumps(v, separators=(', ', ': '))}") for k, v in body.items() if v is not None]
        text = Group(*lines, Text(""), Text(write["reason"], style="yellow"))
        self.c.print(Panel(text, title="dry run: nothing sent", border_style="yellow", expand=False))
        self.pause(4.0)

    def audit(self, doc: dict[str, Any], ids: list[str], lines: int) -> None:
        self.c.print(Rule("audit log, last lines (times UTC)", style="grey35"))
        entries = [json.loads(line) for q in doc["questions"] if q["id"] in ids for line in q["audit"]]
        width = (self.c.width or 100) - 52
        for e in entries[-lines:]:
            args = json.dumps({k: v for k, v in e["args"].items() if v not in (None, "[none]")},
                              separators=(",", ":"))
            if len(args) > width:
                args = args[: width - 3] + "..."
            self.c.print(Text.assemble((e["ts"][11:19] + " ", "dim"), (f"{e['tool']:<24}", "cyan"),
                                       (f"{e['status']:<6}", "green" if e["status"] == "ok" else "red"),
                                       (f"{e['duration_ms']:>6.1f} ms  ", "dim"), (args, "")), no_wrap=True)
        self.c.print(Text("No API key, headers or results are written to the audit log; names are redacted.",
                          style="dim italic"))
        self.pause(4.0)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ngpvan-mcp-replay")
    p.add_argument("recording", nargs="?", default="demo/recorded_runs.json")
    p.add_argument("--ids", help="comma-separated question ids (default: the five shown in the README demo)")
    p.add_argument("--speed", type=float, default=1.0, help="0 prints everything at once")
    p.add_argument("--audit-lines", type=int, default=8)
    p.add_argument("--width", type=int)
    a = p.parse_args(argv)
    doc = json.loads(Path(a.recording).read_text())
    ids = a.ids.split(",") if a.ids else DEFAULT_IDS
    known = {q["id"] for q in doc["questions"]}
    missing = [i for i in ids if i not in known]
    if missing:
        print(f"not in the recording: {', '.join(missing)}", file=sys.stderr)
        return 2
    player = Player(Console(width=a.width, highlight=False), a.speed)
    player.header(doc)
    for q in doc["questions"]:
        if q["id"] in ids:
            player.question(q)
    player.audit(doc, ids, a.audit_lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
