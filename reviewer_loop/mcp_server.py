"""Our MCP server. It exposes three tools to any MCP client over stdio.

Run directly with:  python -m reviewer_loop.mcp_server

stdio transport: the client writes JSON-RPC requests to this process's stdin and reads responses from
its stdout. stdout is therefore reserved for the protocol, and anything we log must go to stderr.
"""
import logging

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from reviewer_loop import reviewer, sandbox, static_analysis
from reviewer_loop.llm import ProviderError

server = MCPServer("code-review-tools", instructions="Test, lint and cross-model review tools for Python code.",
                   log_level="WARNING")
for noisy in ("httpx", "google_genai"):  # per-request INFO logs (they go to stderr, but clutter the console)
    logging.getLogger(noisy).setLevel(logging.WARNING)


@server.tool()
def run_tests(solution_code: str, test_code: str) -> dict:
    """Run the pytest tests in `test_code` against `solution_code` (saved as solution.py) in a sandbox.
    Returns status (passed/failed/error/timeout), counts, and per-test outcomes with failure messages."""
    return sandbox.run_tests(solution_code, test_code)


@server.tool()
def run_static_analysis(solution_code: str, test_code: str) -> dict:
    """Run ruff (pyflakes, bugbear and bandit security rules) on both files. Each issue is marked blocking or advisory."""
    return static_analysis.run_ruff(solution_code, test_code)


@server.tool()
def review_code(task: str, solution_code: str, test_code: str, test_results: dict, lint_results: dict,
                ledger: list[dict]) -> dict:
    """Review the code with a Gemini model (a different provider from the generator). The reviewer sees the
    test and static-analysis results, retrieved coding-standard sections, and the ledger of earlier findings.
    Returns a validated structured review: findings with severity, location, rationale and suggested fix."""
    # Anticipated failures are raised as ToolError: the client receives an error result (is_error=True) with our
    # message. Any other exception is treated by the SDK as a crash and the client only sees a generic message.
    try:
        return reviewer.review(task, solution_code, test_code, test_results, lint_results, ledger)
    except (ProviderError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


if __name__ == "__main__":
    server.run()  # defaults to the stdio transport
