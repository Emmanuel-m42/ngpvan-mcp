from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from ngpvan_mcp.audit import AuditLog
from ngpvan_mcp.client import VanClient
from ngpvan_mcp.config import Config
from ngpvan_mcp.demo.fake_van import DEMO_API_KEY, DEMO_APP_NAME, FakeVan, load_demo_data
from ngpvan_mcp.ratelimit import TokenBucket
from ngpvan_mcp.secrets import ApiKey
from ngpvan_mcp.server import build_server
from ngpvan_mcp.tools import Toolbox


async def no_sleep(_: float) -> None:
    return None


@pytest.fixture
def demo_data() -> dict[str, Any]:
    # A private copy per test, so no test can change another's data.
    return copy.deepcopy(load_demo_data())


@pytest.fixture
def fake(demo_data) -> FakeVan:
    return FakeVan(demo_data)


def make_van(fake: FakeVan) -> VanClient:
    return VanClient(
        app_name=DEMO_APP_NAME,
        api_key=ApiKey(DEMO_API_KEY),
        transport=fake.transport(),
        bucket=TokenBucket(1000, 1000, sleep=no_sleep),
        sleep=no_sleep,
    )


@pytest.fixture
def van(fake) -> VanClient:
    return make_van(fake)


@pytest.fixture
def config() -> Config:
    return Config(audit_log=None)


@pytest.fixture
def tb(van, config) -> Toolbox:
    return Toolbox(van=van, config=config)


@pytest.fixture
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "audit.jsonl"


@pytest.fixture
def server_factory(fake, audit_path):
    def build(config: Config | None = None):
        cfg = config or Config(audit_log=None)
        return build_server(cfg, make_van(fake), AuditLog(audit_path))

    return build


def read_audit(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
