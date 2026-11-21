"""Command line entry point: `ngpvan-mcp`.

    ngpvan-mcp --demo                       # stdio, fake Riverbend data, no key
    ngpvan-mcp --config ngpvan-mcp.toml     # stdio, real VAN (key from env)
    ngpvan-mcp --transport http --port 8000 # streamable HTTP at /mcp
"""

from __future__ import annotations

import argparse
import hmac
import os
import sys

from .audit import AuditLog
from .client import VanClient
from .config import Config, ConfigError
from .demo.fake_van import DEMO_API_KEY, DEMO_APP_NAME, FakeVan
from .ratelimit import TokenBucket
from .secrets import ApiKey, MissingKeyError, load_api_key
from .server import build_server


def make_client(config: Config, demo: bool, fake: FakeVan | None = None) -> VanClient:
    bucket = TokenBucket(config.limits.requests_per_second, config.limits.burst)
    if demo:
        fake = fake or FakeVan()
        return VanClient(app_name=DEMO_APP_NAME, api_key=ApiKey(DEMO_API_KEY), transport=fake.transport(),
                         bucket=bucket, max_retries=config.limits.max_retries)
    app_name = os.environ.get("VAN_APP_NAME", config.app_name)
    return VanClient(app_name=app_name, api_key=load_api_key(config.database_mode), base_url=config.base_url,
                     bucket=bucket, max_retries=config.limits.max_retries, timeout=config.limits.timeout_seconds)


class BearerAuth:
    """Plain ASGI middleware: every HTTP request needs `Authorization: Bearer <token>`."""

    def __init__(self, app, token: str) -> None:
        self.app = app
        self.token = token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            header = dict(scope.get("headers") or []).get(b"authorization", b"")
            supplied = header[7:] if header.lower().startswith(b"bearer ") else b""
            if not hmac.compare_digest(supplied, self.token):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
                return
        await self.app(scope, receive, send)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ngpvan-mcp", description="MCP server for the NGP VAN API, read-only by default.")
    parser.add_argument("--config", help="TOML settings file (see ngpvan-mcp.example.toml)")
    parser.add_argument("--demo", action="store_true", help="use the built-in fake Riverbend data instead of VAN")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    try:
        config = Config.load(args.config)
        client = make_client(config, args.demo)
        # "stderr" sends audit lines to the platform's log collector (Cloud Run,
        # systemd). stdout is never used: on stdio it carries the protocol.
        audit = AuditLog(stream=sys.stderr) if config.audit_log == "stderr" else AuditLog(config.audit_log)
    except (ConfigError, MissingKeyError) as exc:
        print(f"ngpvan-mcp: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"ngpvan-mcp: can't write the audit log ({exc}); not starting without one", file=sys.stderr)
        return 2
    server = build_server(config, client, audit)

    if args.transport == "stdio":
        server.run("stdio")
        return 0

    token = os.environ.get("NGPVAN_MCP_HTTP_TOKEN")
    if not token:
        print("ngpvan-mcp: set NGPVAN_MCP_HTTP_TOKEN; the HTTP transport doesn't run without a bearer token",
              file=sys.stderr)
        return 2
    import uvicorn

    app = BearerAuth(server.streamable_http_app(host=args.host), token)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
