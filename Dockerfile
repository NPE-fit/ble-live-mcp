# Hosted BLE Live MCP server: streamable HTTP, read-only, bearer token required.
#
#   docker build -t ble-live-mcp -f mcp/Dockerfile .
#   docker run -p 8765:8765 -e BLE_LIVE_MCP_TOKEN=change-me ble-live-mcp
#
# Build from the repository root so the templates (ble_live_base.c/.h) and live.xlsx ride along
# for generate_firmware and example_config.
FROM python:3.12-slim

WORKDIR /app
COPY mcp/pyproject.toml mcp/README.md /app/mcp/
COPY mcp/src /app/mcp/src
COPY ble_live_base.c ble_live_base.h live.xlsx /app/
RUN pip install --no-cache-dir /app/mcp

ENV BLE_LIVE_ROOT=/app
EXPOSE 8765
CMD ["ble-live-mcp", "--transport", "http", "--host", "0.0.0.0", "--port", "8765", "--read-only"]
