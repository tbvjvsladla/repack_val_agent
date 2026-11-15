#!/usr/bin/env python3
"""Run this skill's tools with its isolated Python environment."""

import argparse
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import sys
from typing import Any


def find_project_root(skill_root: Path) -> Path:
    """Return the project whose envs/ holds the keys.

    DEEPEVAL_PACK_PROJECT_ROOT wins. Otherwise the skill folder decides:
    <root>/skills/<name>, <root>/.agents/skills/<name> or <root>/.claude/skills/<name>.
    Adapter symlinks resolve to the canonical folder first. Any other place uses the
    current directory.
    """
    override = os.environ.get("DEEPEVAL_PACK_PROJECT_ROOT")
    if override:
        return Path(override).resolve()
    skills_dir = skill_root.parent
    if skills_dir.name == "skills":
        if skills_dir.parent.name in {".agents", ".claude"}:
            return skills_dir.parent.parent
        return skills_dir.parent
    return Path.cwd().resolve()


def main() -> int:
    """Start MCP or emit local configuration validation as JSON."""
    skill_root = Path(__file__).resolve().parents[1]
    project_root = find_project_root(skill_root)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["serve", "check"])
    parser.add_argument("--config", type=Path, default=skill_root / "config.yaml")
    parser.add_argument("--env-file", type=Path, default=project_root / "envs/.env")
    args = parser.parse_args()
    # DeepEval must not discover cloud credentials or dotenv behind the pack's config.
    os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] = "1"
    os.environ["DEEPEVAL_DISABLE_DOTENV"] = "1"
    for key in tuple(os.environ):
        if key.startswith("CONFIDENT_"):
            del os.environ[key]
    sys.path.insert(0, str(skill_root / "src"))
    # Third-party initialization output must not corrupt stdio JSON-RPC.
    with redirect_stdout(sys.stderr):
        from deepeval_pack.server import (
            build_server,
        )  # Environment guard must precede SDK imports.

        server = build_server(args.config.resolve(), args.env_file.resolve())
    if args.command == "serve":
        server.run()
        return 0
    from deepeval_pack.config import load_config  # Guard must precede SDK import.
    from deepeval_pack.engine import validate_config  # Guard must precede SDK import.
    from deepeval_pack.server import (
        _failure,
    )  # Shared sanitized CLI/MCP error contract.

    try:
        result: dict[str, Any] = {
            "status": "ok",
            **validate_config(load_config(args.config)),
        }
    except Exception as error:  # noqa: BLE001 - CLI shares MCP's sanitized error contract.
        result = _failure(error)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
