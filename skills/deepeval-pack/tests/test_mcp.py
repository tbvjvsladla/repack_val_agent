"""Exercise the actual stdio MCP server without registering a real Judge."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


class MCPTest(unittest.IsolatedAsyncioTestCase):
    """Verify actual protocol discovery, live config reload and unconfigured errors."""

    async def test_stdio_tools_and_unconfigured_evaluation(self):
        skill_root = Path(__file__).resolve().parents[1]  # noqa: ASYNC240
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            config = temp / "config.yaml"
            example = (skill_root / "config.example.yaml").read_text(encoding="utf-8")
            config.write_text(example, encoding="utf-8")
            params = StdioServerParameters(
                command=sys.executable,
                args=[
                    "-B",
                    str(skill_root / "scripts/run.py"),
                    "serve",
                    "--config",
                    str(config),
                    "--env-file",
                    str(temp / "absent.env"),
                ],
                cwd=temp,
            )
            async with (
                stdio_client(params) as (reader, writer),
                ClientSession(reader, writer) as client,
            ):
                await client.initialize()
                tools = await client.list_tools()
                self.assertEqual(  # noqa: PT009
                    {tool.name for tool in tools.tools},
                    {
                        "deepeval_status",
                        "deepeval_validate",
                        "deepeval_evaluate",
                    },
                )
                result = await client.call_tool("deepeval_status", {})
                status = json.loads(result.content[0].text)
                self.assertEqual(status["status"], "ok")  # noqa: PT009
                self.assertFalse(status["judge_configured"])  # noqa: PT009
                result = await client.call_tool("deepeval_validate", {"cases": [{}]})
                self.assertEqual(  # noqa: PT009
                    json.loads(result.content[0].text)["status"], "invalid_input"
                )
                result = await client.call_tool("deepeval_evaluate", {"cases": [{}]})
                evaluation = json.loads(result.content[0].text)
                self.assertEqual(evaluation["error_type"], "ConfigurationError")  # noqa: PT009
                self.assertIn("Judge is not configured", evaluation["error"])  # noqa: PT009
                config.write_text(
                    example.replace("provider: null", "provider: unsupported"),
                    encoding="utf-8",
                )
                result = await client.call_tool("deepeval_status", {})
                self.assertEqual(json.loads(result.content[0].text)["status"], "error")  # noqa: PT009
                config.write_text(example, encoding="utf-8")
                result = await client.call_tool("deepeval_validate", {})
                self.assertEqual(json.loads(result.content[0].text)["status"], "ok")  # noqa: PT009


if __name__ == "__main__":
    unittest.main()
