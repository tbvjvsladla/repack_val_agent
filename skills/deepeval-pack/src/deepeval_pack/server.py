"""Expose evaluation tools over local stdio MCP."""

from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from pydantic import ValidationError

from deepeval_pack import engine
from deepeval_pack.config import ConfigurationError, load_config


def _failure(error: Exception) -> dict[str, Any]:
    if isinstance(error, ValidationError):
        details = [
            {"location": list(item["loc"]), "type": item["type"]}
            for item in error.errors(include_input=False, include_context=False)
        ]
        return {
            "status": "error",
            "error_type": "InvalidConfiguration",
            "details": details,
        }
    if isinstance(error, ConfigurationError):
        return {
            "status": "error",
            "error_type": type(error).__name__,
            "error": str(error),
        }
    return {
        "status": "error",
        "error_type": type(error).__name__,
        "error": "Evaluation setup failed; check Judge credentials and provider settings",
    }


def build_server(config_path: Path, env_file: Path) -> MCPServer:
    """Build tools that reload local config on each request and never expose API keys."""
    server = MCPServer("deepeval-pack")

    @server.tool()
    def deepeval_status() -> dict[str, Any]:
        """List supported metrics/providers and validate config; does not call a Judge."""
        catalog = {
            "supported_metrics": list(engine.METRICS),
            "supported_providers": ["anthropic", "openai", "google", "deepseek"],
        }
        try:
            return {
                "status": "ok",
                **catalog,
                **engine.validate_config(load_config(config_path)),
            }
        except (ConfigurationError, ValidationError) as error:
            return {**catalog, **_failure(error)}

    @server.tool()
    def deepeval_validate(cases: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Validate config and optional input cases locally, without a Judge API call."""
        try:
            config = load_config(config_path)
            status = engine.validate_config(config)
            errors = engine.validate_cases(config, cases) if cases is not None else []
        except (ConfigurationError, ValidationError) as error:
            return _failure(error)
        return {
            "status": "invalid_input" if errors else "ok",
            **status,
            "input_errors": errors,
        }

    @server.tool()
    async def deepeval_evaluate(cases: list[dict[str, Any]]) -> dict[str, Any]:
        """Evaluate supplied cases with the configured Judge. Sends case text to its provider."""
        try:
            return await engine.evaluate(load_config(config_path), cases, env_file)
        except Exception as error:  # noqa: BLE001 - MCP returns a bounded error without leaking SDK credentials.
            return _failure(error)

    return server
