"""An agent outside this harness as the model: a tool CLI it calls from a shell, and a replayer that scores it.

The answering agent (for example a Claude subagent in a coding tool) gets the system prompt and the tool
list from `ngpvan-agent-tool prompt`, calls the real tools with

    ngpvan-agent-tool <tool> '<json args>' --case d01 --trace run1.jsonl

which prints exactly the text the model loop would put in the tool message, and appends the call to the
trace. Its final answers go in a JSON file, {"d01": "answer text", ...}. Then

    ngpvan-mcp-evals --provider agent-trace --trace run1.jsonl --answers run1.json --split dev

replays each case's calls, in order, through a fresh demo server inside the normal eval loop and scores
the answer with the usual scorer. Several --trace/--answers pairs give pass^k. The recorder takes the
same pair (`ngpvan-mcp-record --provider agent-trace ...`) to build demo/recorded_runs.json.

What differs from the in-loop providers: the agent runs its own loop, so the 6-step cap isn't applied
(each traced call replays as one step), and seconds are replay time, not model time. A replayed result
that doesn't match what the agent was shown fails the case, so a trace can't be edited after the fact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import anyio
from mcp import Client

from .. import tools as toolbox_module
from ..audit import AuditLog
from ..cli import make_client
from ..config import Config
from ..server import INSTRUCTIONS, build_server
from .agent import SYSTEM, mcp_tools

AGENT_LABEL = ("Claude Opus 5.5 as the agent through the tool CLI (same tools and prompt, "
               "not an in-app agent loop)")


def _contexts() -> dict[str, dict[str, Any]]:
    """Case id -> today and writes setting, for the eval cases and the demo prompts."""
    from .record import PROMPTS, TODAY
    from .runner import load_cases

    spec = load_cases()
    out = {c["id"]: {"today": spec["today"], "writes": False} for c in spec["cases"]}
    for p in PROMPTS:
        out[p["id"]] = {"today": TODAY, "writes": bool(p.get("writes_enabled"))}
        if p.get("followup"):
            out[f"{p['id']}.followup"] = out[p["id"]]
    return out


def _server(writes: bool, case: str = ""):
    config = Config(audit_log=None, writes={"apply_canvass_response": writes})
    # Each CLI call is a fresh server, so a dry run's confirm token would never match the send that follows it.
    # Within one case the CLI derives the token secret from the case id; the replay uses a real random one.
    seed = hashlib.sha256(f"ngpvan-agent-tool:{case}".encode()).digest()
    with patch.object(toolbox_module.secrets, "token_bytes", lambda n: seed[:n]):
        return build_server(config, make_client(config, demo=True), AuditLog(None))


async def _prompt(case: str | None) -> str:
    ctx = _contexts().get(case or "", {"today": _contexts()["d01"]["today"], "writes": False})
    async with Client(_server(ctx["writes"])) as client:
        tools = await mcp_tools(client)
        system = SYSTEM.format(today=ctx["today"], instructions=client.instructions or INSTRUCTIONS)
    return f"SYSTEM PROMPT\n\n{system}\n\nTOOLS (name, description, JSON schema of the arguments)\n\n" + \
        json.dumps(tools, indent=1)


async def _call(tool: str, args: dict[str, Any], writes: bool, case: str) -> tuple[str, bool]:
    async with Client(_server(writes, case.removesuffix(".followup"))) as client:
        result = await client.call_tool(tool, args)
    return (result.content[0].text if result.content else ""), bool(result.is_error)


def tool_main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ngpvan-agent-tool", description="Call one ngpvan-mcp tool as the model would.")
    p.add_argument("tool", help="a tool name, or 'prompt' to print the system prompt and tool list")
    p.add_argument("args", nargs="?", default="{}", help="the tool's arguments as a JSON object")
    p.add_argument("--case", help="case id the call belongs to")
    p.add_argument("--trace", help="JSONL file the call is appended to")
    a = p.parse_args(argv)
    if a.tool == "prompt":
        print(anyio.run(_prompt, a.case))
        return 0
    contexts = _contexts()
    if not a.case or a.case not in contexts:
        print("--case must be one of the case ids in your brief", file=sys.stderr)
        return 2
    if not a.trace:
        print("--trace is required", file=sys.stderr)
        return 2
    try:
        args = json.loads(a.args)
    except json.JSONDecodeError as exc:
        print(f"arguments aren't valid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(args, dict):
        print("arguments must be a JSON object", file=sys.stderr)
        return 2
    text, is_error = anyio.run(_call, a.tool, args, contexts[a.case]["writes"], a.case)
    entry = {"case": a.case, "tool": a.tool, "args": args, "result": text, "is_error": is_error,
             "at": datetime.now(UTC).isoformat(timespec="seconds")}
    with open(a.trace, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(text)
    return 0


# -- replaying a trace as a model ----------------------------------------------


def load_run(trace: Path | None, answers: Path) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    calls: dict[str, list[dict[str, Any]]] = {}
    if trace and trace.exists():
        for line in trace.read_text("utf-8").splitlines():
            if line.strip():
                entry = json.loads(line)
                calls.setdefault(entry["case"], []).append(entry)
    raw = json.loads(answers.read_text("utf-8"))
    return calls, {str(k): str(v) for k, v in raw.items()}


def _comparable(text: str) -> str:
    """A tool result with the per-server random parts (a dry run's confirm token) blanked."""
    try:
        value = json.loads(text)
    except ValueError:
        return text
    if isinstance(value, dict) and "confirm_token" in value:
        value["confirm_token"] = "-"
    return json.dumps(value, sort_keys=True)


class TraceChat:
    """Plays an agent's recorded tool calls and answer through the ChatModel interface.

    Trial n of a case uses run n. Each recorded call is its own step; the tool results the real server
    returns now must match what the agent was shown, or the case ends with an error.
    """

    def __init__(self, runs: list[tuple[dict[str, list[dict[str, Any]]], dict[str, str]]],
                 questions: dict[str, str], name: str = "claude-agent") -> None:
        self.name = name
        self.runs = runs
        self.by_question = {q: cid for cid, q in questions.items()}
        self.current: str | None = None
        self.trial: dict[str, int] = {}
        self.mismatches: list[dict[str, Any]] = []

    def use(self, case_id: str) -> None:
        """For the recorder, where two prompts share a question."""
        self.current = case_id

    async def chat(self, messages, tools):
        users = [i for i, m in enumerate(messages) if m["role"] == "user"]
        cid = self.current or self.by_question[messages[users[0]]["content"]]
        turn = messages[users[-1] + 1:]
        step = sum(1 for m in turn if m["role"] == "assistant")
        if step == 0 and len(users) == 1:
            self.trial[cid] = self.trial.get(cid, -1) + 1
        n = self.trial[cid]
        if n >= len(self.runs):
            raise RuntimeError(f"no run {n + 1} for {cid}")
        if len(users) > 1:  # the organizer's follow-up turn in the recorder
            cid = f"{cid}.followup"
        calls, answers = self.runs[n][0].get(cid, []), self.runs[n][1]
        seen = [m for m in turn if m["role"] == "tool"]
        for i, m in enumerate(seen):
            if _comparable(m["content"]) != _comparable(calls[i]["result"]):
                self.mismatches.append({"case": cid, "run": n, "call": i, "tool": calls[i]["tool"]})
                raise RuntimeError(f"replay mismatch on call {i + 1} ({calls[i]['tool']}): the result differs "
                                   "from what the agent was shown")
        if step < len(calls):
            call = calls[step]
            args = dict(call["args"])
            if "confirm_token" in args:
                # A send after a dry run: the token is per server, so use the one this replay's dry run gave.
                for m in reversed([m for m in messages if m["role"] == "tool"]):
                    try:
                        token = json.loads(m["content"]).get("confirm_token")
                    except (ValueError, AttributeError):
                        token = None
                    if token:
                        args["confirm_token"] = token
                        break
            return {"content": "", "tool_calls": [{"id": f"t{step}", "name": call["tool"], "arguments": args}]}
        if cid not in answers:
            raise RuntimeError(f"no answer for {cid} in run {n + 1}")
        return {"content": answers[cid], "tool_calls": []}

    def max_steps(self) -> int:
        longest = max((len(c) for calls, _ in self.runs for c in calls.values()), default=0)
        return longest + 1


def trace_runs(traces: list[str] | None, answers: list[str] | None):
    traces, answers = traces or [], answers or []
    if not answers:
        raise SystemExit("--provider agent-trace needs at least one --answers file (and its --trace)")
    if traces and len(traces) != len(answers):
        raise SystemExit("give one --trace per --answers file, in the same order")
    return [load_run(Path(t) if traces else None, Path(a)) for t, a in zip(traces or [None] * len(answers), answers)]


def add_trace_args(p) -> None:
    p.add_argument("--trace", action="append", help="agent-trace: a JSONL trace from ngpvan-agent-tool (repeatable)")
    p.add_argument("--answers", action="append", help="agent-trace: the answers JSON for the trace in the same position")


if __name__ == "__main__":
    raise SystemExit(tool_main())
