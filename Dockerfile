# ngpvan-mcp over streamable HTTP, for Cloud Run or any container host.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --extra gcp

COPY deploy/cloudrun.toml /app/ngpvan-mcp.toml
RUN useradd --uid 10001 --no-create-home app
USER 10001

ENV PORT=8080
CMD ["sh", "-c", "exec /app/.venv/bin/ngpvan-mcp --config /app/ngpvan-mcp.toml --transport http --host 0.0.0.0 --port ${PORT}"]
