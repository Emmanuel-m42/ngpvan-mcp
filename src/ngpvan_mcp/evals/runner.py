"""Run the eval set: `ngpvan-mcp-evals --model qwen2.5:7b --split dev --k 3`.

Each case runs k times against a fresh demo server (writes off, initials
policy). Reports go to evals/reports/<label>.json and .md:

- pass@1: the share of all runs that passed.
- pass^k: the share of cases that passed on every one of their k runs,
  which is what matters when an organizer asks the same thing every day.
"""

from __future__ import annotations

import argparse
import json
import platform
import time
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

import anyio
from mcp import Client

from ..audit import AuditLog
from ..cli import make_client
from ..config import Config
from ..demo.fake_van import load_demo_data
from ..server import INSTRUCTIONS, build_server
from .agent import AnthropicChat, ChatModel, OllamaChat, mcp_tools, run_case
from .scoring import score
from .scripted import PLANS, ScriptedChat
from .truth import TRUTHS, pii_values


def load_cases() -> dict[str, Any]:
    return json.loads(resources.files("ngpvan_mcp.evals").joinpath("cases.json").read_text("utf-8"))


def summarize(results: list[dict[str, Any]], k: int) -> dict[str, Any]:
    by_case: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        by_case.setdefault(r["id"], []).append(r)

    def block(case_ids: list[str]) -> dict[str, Any]:
        runs = [r for cid in case_ids for r in by_case[cid]]
        if not runs:
            return {"cases": 0}
        checks: dict[str, list[bool]] = {}
        for r in runs:
            for name, ok in r["checks"].items():
                checks.setdefault(name, []).append(ok)
        return {
            "cases": len(case_ids),
            "runs": len(runs),
            "pass_at_1": round(sum(r["passed"] for r in runs) / len(runs), 3),
            f"pass_hat_{k}": round(sum(all(r["passed"] for r in by_case[c]) for c in case_ids) / len(case_ids), 3),
            "checks": {n: round(sum(v) / len(v), 3) for n, v in sorted(checks.items())},
            "median_seconds": sorted(r["seconds"] for r in runs)[len(runs) // 2],
        }

    ids = list(by_case)
    out = {"all": block(ids)}
    for split in ("dev", "heldout"):
        out[split] = block([c for c in ids if by_case[c][0]["split"] == split])
    for cat in ("answer", "refuse_pii", "refuse_write", "refuse_missing"):
        out[cat] = block([c for c in ids if by_case[c][0]["category"] == cat])
    return out


def markdown(report: dict[str, Any]) -> str:
    m, k = report["meta"], report["meta"]["k"]
    lines = [
        f"# Eval run: {m['model']}",
        "",
        f"- Provider: {m['provider']}; hardware: {m['hardware']}",
        f"- Split: {m['split']}; k = {k}; temperature {m['temperature']}; started {m['started']}",
        "- Server: demo data, writes off, names as initials",
        "",
    ]
    if m["provider"] == "scripted":
        lines += ["This is a harness check: a fixed plan per question, not a model. It shows the runner, the "
                  "scorer and the tools work together and that each tool's output carries the facts the answer "
                  "key expects. It says nothing about how a model would do.", ""]
    lines += [
        f"| Slice | Cases | pass@1 | pass^{k} | tools | facts | citation | refusal | privacy | median s |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, b in report["summary"].items():
        if not b.get("cases"):
            continue
        c = b["checks"]
        cell = lambda key: f"{c[key]:.2f}" if key in c else ""  # noqa: E731
        lines.append(f"| {name} | {b['cases']} | {b['pass_at_1']:.2f} | {b[f'pass_hat_{k}']:.2f} | {cell('tools')} | "
                     f"{cell('facts')} | {cell('citation')} | {cell('refusal')} | {cell('privacy')} | {b['median_seconds']} |")
    lines += ["", "## Runs", "", "| Case | Split | Pass | Failed checks | Tools called | Seconds |", "|---|---|---|---|---|---|"]
    for r in report["results"]:
        failed = ", ".join(n for n, ok in r["checks"].items() if not ok) or ""
        if r["error"]:
            failed = f"error: {r['error'][:60]}"
        lines.append(f"| {r['id']} | {r['split']} | {'yes' if r['passed'] else 'no'} | {failed} | "
                     f"{', '.join(r['tools_called'])} | {r['seconds']} |")
    return "\n".join(lines) + "\n"


async def run(model: ChatModel, split: str, k: int, out_dir: Path, label: str, meta: dict[str, Any],
              case_ids: list[str] | None = None) -> dict[str, Any]:
    spec = load_cases()
    data = load_demo_data()
    pii = pii_values(data)
    cases = [c for c in spec["cases"] if split == "all" or c["split"] == split]
    if case_ids:
        cases = [c for c in cases if c["id"] in case_ids]
    results = []
    for case in cases:
        facts = TRUTHS[case["truth"]](data) if case["truth"] else []
        for trial in range(k):
            config = Config(audit_log=None)
            server = build_server(config, make_client(config, demo=True), AuditLog(None))
            async with Client(server) as client:
                tools = await mcp_tools(client)
                instructions = client.instructions or INSTRUCTIONS
                outcome = await run_case(model, client, tools, instructions, case["question"], spec["today"])
            result = score(case, outcome, facts, pii) | {"trial": trial}
            results.append(result)
            print(f"{case['id']} trial {trial}: {'pass' if result['passed'] else 'FAIL'} "
                  f"{result['checks']} {result['seconds']}s", flush=True)
    report = {"meta": meta | {"finished": datetime.now(UTC).isoformat(timespec="seconds")},
              "summary": summarize(results, k), "results": results}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{label}.json").write_text(json.dumps(report, indent=1) + "\n")
    (out_dir / f"{label}.md").write_text(markdown(report))
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ngpvan-mcp-evals")
    p.add_argument("--provider", choices=["ollama", "anthropic", "vertex", "scripted"], default="ollama",
                   help="scripted: a fixed plan per question, for testing the harness (not a model)")
    p.add_argument("--model", default="scripted-oracle")
    p.add_argument("--ollama-host", default="http://127.0.0.1:11434")
    p.add_argument("--split", choices=["dev", "heldout", "all"], default="dev")
    p.add_argument("--k", type=int, default=1)
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--num-ctx", type=int, default=8192)
    p.add_argument("--hardware", default=platform.processor() or platform.machine())
    p.add_argument("--cases", help="comma-separated case ids, for a quick check")
    p.add_argument("--out", default="evals/reports")
    p.add_argument("--label")
    p.add_argument("--vertex-region")
    p.add_argument("--vertex-project")
    a = p.parse_args(argv)

    if a.provider == "scripted":
        questions = {c["id"]: c["question"] for c in load_cases()["cases"]}
        model: ChatModel = ScriptedChat(PLANS, questions)
        a.hardware = "no model (scripted plan)"
    elif a.provider == "ollama":
        model = OllamaChat(a.model, a.ollama_host, a.temperature, a.num_ctx)
    else:
        model = AnthropicChat(a.model, vertex=a.provider == "vertex", region=a.vertex_region,
                              project=a.vertex_project, temperature=a.temperature)
    label = a.label or f"{a.model.replace(':', '-').replace('/', '-')}_{a.split}_k{a.k}_{time.strftime('%Y%m%d-%H%M')}"
    meta = {"model": a.model, "provider": a.provider, "hardware": a.hardware, "split": a.split, "k": a.k,
            "temperature": a.temperature, "num_ctx": a.num_ctx,
            "started": datetime.now(UTC).isoformat(timespec="seconds")}
    ids = a.cases.split(",") if a.cases else None
    report = anyio.run(run, model, a.split, a.k, Path(a.out), label, meta, ids)
    print(json.dumps(report["summary"]["all"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
