"""Chat with your VAN data through a local open model or an OpenAI-compatible API.

Starts ngpvan-mcp over stdio (the same way Claude Desktop does), hands its
tools to the model, and loops: you ask, the model calls tools, you get the
answer and the list of tools it used.

    ollama pull qwen3:14b
    uv run python examples/ollama_chat.py --demo --model qwen3:14b

    # DeepSeek's hosted API (key in DEEPSEEK_API_KEY)
    uv run python examples/ollama_chat.py --demo --provider openai --model deepseek-v4-pro

Drop --demo and set VAN_APP_NAME / VAN_API_KEY to use a real VAN key.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date

import anyio
from mcp import Client, StdioServerParameters

from ngpvan_mcp.evals.agent import OllamaChat, add_openai_args, make_openai, mcp_tools, run_case


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--provider", choices=["ollama", "openai"], default="ollama")
    p.add_argument("--model", default="qwen3:14b")
    p.add_argument("--ollama-host", default=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"))
    p.add_argument("--temperature", type=float, default=0.0)
    add_openai_args(p)
    p.add_argument("--config")
    p.add_argument("--demo", action="store_true")
    a = p.parse_args()

    args = ["-m", "ngpvan_mcp.cli"] + (["--demo"] if a.demo else []) + (["--config", a.config] if a.config else [])
    # The MCP server gets the environment minus the model API key; it never needs it.
    env = {k: v for k, v in os.environ.items() if k != a.api_key_env}
    server = StdioServerParameters(command=sys.executable, args=args, env=env)
    today = "Sunday, October 18, 2026" if a.demo else date.today().strftime("%A, %B %d, %Y")
    model = make_openai(a) if a.provider == "openai" else OllamaChat(a.model, a.ollama_host, a.temperature)
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
