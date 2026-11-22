FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 \
    VBRW_AUDIT_LOG=/audit/audit.jsonl FASTMCP_SHOW_SERVER_BANNER=false

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv pip install --system --no-cache . && useradd --uid 10001 --create-home mcp && mkdir /audit && chown mcp /audit

USER mcp
VOLUME ["/audit"]
ENTRYPOINT ["vbr-write-mcp"]
