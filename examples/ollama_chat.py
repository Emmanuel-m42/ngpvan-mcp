"""Chat with your VAN data through a local open model.

Starts ngpvan-mcp over stdio (the same way Claude Desktop does), hands its
tools to a model served by Ollama, and loops: you ask, the model calls
tools, you get the answer and the list of tools it used.

    ollama pull qwen2.5:7b
    uv run python examples/ollama_chat.py --demo --model qwen2.5:7b

Drop --demo and set VAN_APP_NAME / VAN_API_KEY to use a real VAN key.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date

import anyio
from mcp import Client, StdioServerParameters

from ngpvan_mcp.evals.agent import OllamaChat, mcp_tools, run_case


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="qwen2.5:7b")
    p.add_argument("--ollama-host", default=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"))
    p.add_argument("--config")
    p.add_argument("--demo", action="store_true")
    a = p.parse_args()

    args = ["-m", "ngpvan_mcp.cli"] + (["--demo"] if a.demo else []) + (["--config", a.config] if a.config else [])
    server = StdioServerParameters(command=sys.executable, args=args, env=dict(os.environ))
    today = "Friday, November 20, 2026" if a.demo else date.today().strftime("%A, %B %d, %Y")
    model = OllamaChat(a.model, a.ollama_host)
    async with Client(server) as client:
        tools = await mcp_tools(client)
        instructions = client.instructions or ""
        print(f"{len(tools)} tools from ngpvan-mcp. Ask a question, or press Enter to quit.")
        while True:
            question = input("\n> ").strip()
            if not question:
                return
            run = await run_case(model, client, tools, instructions, question, today)
            print(run.answer or f"(no answer: {run.error})")
            if run.tool_calls:
                print("tools used:", ", ".join(c["name"] for c in run.tool_calls))


if __name__ == "__main__":
    anyio.run(main)
