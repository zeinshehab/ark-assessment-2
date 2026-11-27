"""An MCP server that dies in the middle of a tool call, like a real crash (segfault, OOM kill).

Used by tests/test_reliability.py to check that a run ends cleanly with error_type "tool_failure" instead of
hanging or taking the API worker down.
"""
import os

from mcp.server.mcpserver import MCPServer

server = MCPServer("crashing-tools")


@server.tool()
def run_tests(solution_code: str, test_code: str) -> dict:
    os._exit(1)  # no cleanup, no error response: the process is simply gone


if __name__ == "__main__":
    server.run()
