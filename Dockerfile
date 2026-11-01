# Hosted BLE Live MCP server: streamable HTTP, read-only, bearer token required.
#
#   docker build -t ble-live-mcp .
#   docker run -p 8765:8765 -e BLE_LIVE_MCP_TOKEN=change-me ble-live-mcp
#
# To enable generate_firmware, mount a checkout of NPE's reference design and point
# BLE_LIVE_ROOT at it:  -v /path/to/ble_live:/refdesign:ro -e BLE_LIVE_ROOT=/refdesign
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md /app/
COPY src /app/src
RUN pip install --no-cache-dir /app

EXPOSE 8765
CMD ["ble-live-mcp", "--transport", "http", "--host", "0.0.0.0", "--port", "8765", "--read-only"]
