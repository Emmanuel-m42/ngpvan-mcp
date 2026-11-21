"""An append-only JSON Lines record of every tool call.

One line per call: when, which tool, the arguments with personal values
redacted, the outcome, how long it took and how many VAN requests it made.
The API key, request headers and result contents are never written.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

import anyio

from .privacy import mask_args, mask_text


DEFAULT_PATH = Path("~/.local/state/ngpvan-mcp/audit.jsonl")


class AuditLog:
    """Raises at construction if the file can't be written, so the server
    never starts in a state where calls would go unrecorded."""

    def __init__(self, path: str | Path | None = None, stream: TextIO | None = None) -> None:
        self.path = Path(path).expanduser() if path else None
        self.stream = stream
        self._lock = anyio.Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8"):
                pass

    async def record(
        self,
        *,
        tool: str,
        args: dict[str, Any],
        status: str,
        started: float,
        van_requests: int = 0,
        retries: int = 0,
        items: int | None = None,
        truncated: bool = False,
        error_kind: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "tool": tool,
            "args": mask_args(args),
            "status": status,
            "duration_ms": round((time.monotonic() - started) * 1000, 1),
            "van_requests": van_requests,
            "retries": retries,
        }
        if items is not None:
            entry["items"] = items
        if truncated:
            entry["truncated"] = True
        if error_kind:
            entry["error_kind"] = error_kind
        if error:
            entry["error"] = mask_text(error)[:300]
        line = json.dumps(entry, ensure_ascii=False)
        async with self._lock:
            if self.path:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            if self.stream:
                self.stream.write(line + "\n")
                self.stream.flush()
        return entry


def stderr_log() -> AuditLog:
    return AuditLog(stream=sys.stderr)
