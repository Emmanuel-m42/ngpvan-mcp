"""Settings: a TOML file for behaviour, the environment for credentials.

The API key is never read from the TOML file. It comes from the environment
(`VAN_API_KEY`) or from Google Secret Manager (`VAN_API_KEY_SECRET`), so the
config file can be committed and reviewed like any other code.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .privacy import NamePolicy

WRITE_TOOLS = ("apply_canvass_response",)


@dataclass(frozen=True)
class Limits:
    requests_per_second: float = 5.0
    burst: int = 5
    max_retries: int = 4
    max_items: int = 25
    max_result_chars: int = 20_000
    max_people_per_history_call: int = 25
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class Config:
    app_name: str = "ngpvan-mcp"
    database_mode: int = 1  # 0 My Voters, 1 My Campaign
    base_url: str = "https://api.securevan.com/v4"
    names: NamePolicy = "initial"
    writes: dict[str, bool] = field(default_factory=dict)
    audit_log: str | None = "ngpvan-mcp-audit.jsonl"
    limits: Limits = field(default_factory=Limits)

    def write_enabled(self, tool: str) -> bool:
        return bool(self.writes.get(tool, False))

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> Config:
        van = data.get("van", {})
        privacy = data.get("privacy", {})
        names = privacy.get("names", "initial")
        if names not in ("full", "initial", "none"):
            raise ConfigError(f"privacy.names must be full, initial or none, not {names!r}")
        mode = int(van.get("database_mode", 1))
        if mode not in (0, 1):
            raise ConfigError("van.database_mode must be 0 (My Voters) or 1 (My Campaign)")
        writes = {k: bool(v) for k, v in data.get("writes", {}).items()}
        unknown = set(writes) - set(WRITE_TOOLS)
        if unknown:
            raise ConfigError(f"unknown write tool(s) in [writes]: {', '.join(sorted(unknown))}")
        if "api_key" in van or "api_key" in data:
            raise ConfigError("put the API key in VAN_API_KEY or Secret Manager, not in the config file")
        limits = Limits(**data.get("limits", {}))
        audit = data.get("audit", {}).get("path", "ngpvan-mcp-audit.jsonl")
        return cls(
            app_name=van.get("app_name", cls.app_name),
            database_mode=mode,
            base_url=van.get("base_url", cls.base_url),
            names=names,
            writes=writes,
            audit_log=audit or None,
            limits=limits,
        )

    @classmethod
    def load(cls, path: str | os.PathLike[str] | None) -> Config:
        if path is None:
            return cls()
        with Path(path).open("rb") as fh:
            return cls.from_mapping(tomllib.load(fh))


class ConfigError(ValueError):
    pass
