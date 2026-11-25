"""Our MCP client: starts the MCP server as a subprocess and calls its tools over stdio.

Every call is recorded in `self.calls` so the UI can show exactly which MCP tools were used.
Self-test:  python -m reviewer_loop.mcp_client
"""
import asyncio
import json
import sys
import time

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from . import config


class ToolError(Exception):
    """The MCP tool reported an error (is_error=True), e.g. the reviewer provider failed."""


def _summary(value) -> str:
    text = json.dumps(value) if not isinstance(value, str) else value
    return text if len(text) <= 120 else text[:117] + "..."


class ToolClient:
    def __init__(self):
        params = StdioServerParameters(command=sys.executable, args=["-m", "reviewer_loop.mcp_server"],
                                       cwd=str(config.ROOT))
        self._client = Client(params)
        self.calls: list[dict] = []
        self.iteration = 0  # set by the orchestrator so calls can be grouped per iteration

    async def __aenter__(self):
        await self._client.__aenter__()  # spawns the server and performs the MCP handshake
        return self

    async def __aexit__(self, *exc):
        await self._client.__aexit__(*exc)

    async def list_tools(self) -> list[str]:
        return [tool.name for tool in (await self._client.list_tools()).tools]

    async def call(self, name: str, **arguments) -> dict:
        start = time.perf_counter()
        result = await self._client.call_tool(name, arguments)
        text = "".join(getattr(block, "text", "") for block in result.content)
        data = result.structured_content
        self.calls.append({
            "iteration": self.iteration, "tool": name, "is_error": result.is_error,
            "seconds": round(time.perf_counter() - start, 2),
            "arguments": {k: _summary(v) for k, v in arguments.items()},
            "result": _summary(text if result.is_error else {k: data[k] for k in list(data)[:3]} if data else text),
        })
        if result.is_error:
            raise ToolError(f"{name} failed: {text[:500]}")
        return data if data is not None else json.loads(text)


async def _selftest():
    async with ToolClient() as tools:
        print("tools:", await tools.list_tools())
        solution = "def add(a, b):\n    return a + b\n"
        tests = "from solution import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
        print("run_tests:", await tools.call("run_tests", solution_code=solution, test_code=tests))
        print("run_static_analysis:", await tools.call("run_static_analysis", solution_code=solution, test_code=tests))
        print("call log:", tools.calls)


if __name__ == "__main__":
    asyncio.run(_selftest())
