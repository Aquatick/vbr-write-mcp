.PHONY: install test lint docker-build inspector

install:
	uv sync --all-groups

test:
	uv run --group test pytest -q tests

lint:
	uv run --group dev ruff format src tests && uv run --group dev ruff check src tests

docker-build:
	docker build -f docker/Dockerfile -t vbr-write-mcp .

inspector:
	npx @modelcontextprotocol/inspector uv run vbr-write-mcp
