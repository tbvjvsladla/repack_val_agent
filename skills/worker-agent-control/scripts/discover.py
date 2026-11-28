#!/usr/bin/env python3
"""Read-only native worker discovery. Python stdlib only; no shell, no retries."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from typing import Any

SKILL = Path(__file__).resolve().parents[1]
PROVIDERS = ("claude", "codex")


class DiscoveryError(Exception):
    pass


def default_config_path() -> Path:
    """The local config.json, or the shipped example when no local config exists."""
    local = SKILL / "config.json"
    return local if local.exists() else SKILL / "config.example.json"


def load_worker_config(path: Path, worker: str | None) -> dict[str, Any]:
    """Select a named worker profile, or keep the single-workspace config as it is."""
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        message = "CONFIG_NOT_OBJECT"
        raise DiscoveryError(message)
    profiles = config.get("workers")
    if profiles is not None:
        if not isinstance(profiles, dict) or not profiles:
            message = "WORKERS_NOT_NONEMPTY_OBJECT"
            raise DiscoveryError(message)
        if worker is None:
            raise DiscoveryError("WORKER_REQUIRED:" + ",".join(sorted(profiles)))
        profile = profiles.get(worker)
        if not isinstance(profile, dict):
            raise DiscoveryError("UNKNOWN_WORKER:" + worker)
        config = {key: value for key, value in config.items() if key != "workers"} | profile
        if not isinstance(config.get("workspace"), str) or not config["workspace"].strip():
            raise DiscoveryError("CONFIG_WORKSPACE_MISSING:" + worker)
    elif worker is not None:
        raise DiscoveryError("WORKER_NOT_CONFIGURED:" + worker)
    validate_config(config)
    return config


def validate_config(config: dict[str, Any]) -> None:
    """Reject malformed routing and budgets before a native process can start."""
    if config.get("default_provider") not in PROVIDERS:
        message = "CONFIG_PROVIDER_INVALID"
        raise DiscoveryError(message)
    workspace = config.get("workspace")
    if workspace is not None and (not isinstance(workspace, str) or not workspace.strip()):
        message = "CONFIG_WORKSPACE_INVALID"
        raise DiscoveryError(message)
    contexts = config.get("context_files")
    if not isinstance(contexts, list) or not contexts:
        message = "CONFIG_CONTEXT_FILES_MISSING"
        raise DiscoveryError(message)
    if any(not isinstance(value, str) or not value.strip() for value in contexts):
        message = "CONFIG_CONTEXT_FILES_INVALID"
        raise DiscoveryError(message)
    for key in ("discovery_timeout_seconds", "claude_discovery_max_turns"):
        if key == "claude_discovery_max_turns" and key not in config:
            continue  # Only a Claude discovery uses it; parse_arguments requires it there.
        value = config.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise DiscoveryError("CONFIG_POSITIVE_INTEGER_REQUIRED:" + key)


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def snapshot(workspace: Path) -> dict:
    result = {}
    for key, args in {
        "root": ["rev-parse", "--show-toplevel"],
        "branch": ["rev-parse", "--abbrev-ref", "HEAD"],
        "head": ["rev-parse", "HEAD"],
        "status": ["status", "--porcelain=v1", "--untracked-files=all"],
        "diff": ["diff", "--binary", "--no-ext-diff"],
        "staged_diff": ["diff", "--cached", "--binary", "--no-ext-diff"],
    }.items():
        proc = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(workspace), *args],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if proc.returncode:
            raise DiscoveryError("GIT_SNAPSHOT_FAILED:" + key)
        result[key] = proc.stdout.rstrip("\n")
    if Path(result["root"]).resolve() != workspace:
        raise DiscoveryError("WORKSPACE_NOT_REPOSITORY_ROOT")
    return result


def run_native(argv: list[str], workspace: Path, output: Path, prefix: str,
               timeout: int, prompt: str | None = None) -> int:
    with (output / (prefix + ".stdout")).open("w") as stdout, (output / (prefix + ".stderr")).open("w") as stderr:
        proc = subprocess.Popen(
            argv, cwd=workspace, stdin=subprocess.PIPE if prompt is not None else subprocess.DEVNULL,
            stdout=stdout, stderr=stderr, text=True, start_new_session=True,
        )
        try:
            proc.communicate(input=prompt, timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            if isinstance(exc, KeyboardInterrupt):
                raise
            return 124
        return proc.returncode


def parse_claude(raw: str) -> dict:
    envelope = json.loads(raw)
    if not isinstance(envelope, dict):
        raise DiscoveryError("CLAUDE_ENVELOPE_INVALID")
    if envelope.get("is_error") is not False or envelope.get("errors") or envelope.get("permission_denials"):
        raise DiscoveryError("CLAUDE_CONTROL_FAILED")
    if envelope.get("subtype") != "success":
        raise DiscoveryError("CLAUDE_SUBTYPE_NOT_SUCCESS")
    identity = envelope.get("structured_output")
    if identity is None:
        identity = json.loads(envelope.get("result", ""))
    if not isinstance(identity, dict):
        raise DiscoveryError("IDENTITY_NOT_OBJECT")
    return identity


def parse_codex(events: str, final: str) -> dict:
    completed = False
    for line in events.splitlines():
        event = json.loads(line)
        if not isinstance(event, dict):
            raise DiscoveryError("CODEX_EVENT_INVALID")
        kind = event.get("type", "")
        if kind == "error" or kind == "turn.failed":
            raise DiscoveryError("CODEX_CONTROL_FAILED")
        if kind == "turn.completed":
            completed = True
    if not completed:
        raise DiscoveryError("CODEX_TURN_NOT_COMPLETED")
    identity = json.loads(final)
    if not isinstance(identity, dict):
        raise DiscoveryError("IDENTITY_NOT_OBJECT")
    return identity


def validate_identity(identity: dict, workspace: Path, provider: str,
                      branch: str, context_files: list[str]) -> None:
    if identity.get("workspace_root") != str(workspace) or identity.get("provider") != provider:
        raise DiscoveryError("WORKSPACE_OR_PROVIDER_MISMATCH")
    if identity.get("branch") != branch:
        raise DiscoveryError("BRANCH_MISMATCH")
    read = identity.get("context_files_read")
    if not isinstance(read, list) or not set(context_files).issubset(read):
        raise DiscoveryError("CONTEXT_FILES_MISSING")
    if not isinstance(identity.get("project_purpose"), str) or not identity["project_purpose"].strip():
        raise DiscoveryError("PROJECT_PURPOSE_MISSING")
    rules = identity.get("key_rules")
    if not isinstance(rules, list) or len(rules) < 2 or any(not isinstance(x, str) or not x.strip() for x in rules):
        raise DiscoveryError("KEY_RULES_MISSING")


def apply_config_defaults(parser: argparse.ArgumentParser, args: argparse.Namespace,
                          config: dict[str, Any]) -> None:
    """Fill provider, workspace and timeout from the config; a named profile cannot be overridden."""
    if args.worker is not None:
        if args.provider is not None and args.provider != config["default_provider"]:
            parser.error("WORKER_PROVIDER_OVERRIDE_REJECTED")
        if args.workspace is not None and args.workspace.resolve() != Path(config["workspace"]).resolve():
            parser.error("WORKER_WORKSPACE_OVERRIDE_REJECTED")
    args.provider = args.provider or config["default_provider"]
    if args.provider == "claude" and "claude_discovery_max_turns" not in config:
        parser.error("CONFIG_POSITIVE_INTEGER_REQUIRED:claude_discovery_max_turns")
    if args.workspace is None and config.get("workspace"):
        args.workspace = Path(config["workspace"])
    if args.timeout is None:
        args.timeout = config["discovery_timeout_seconds"]


def check_attempt_paths(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Reject bad budgets and attempt paths before the output directory is created."""
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.provider != "codex" and args.codex_permissions != "read-only":
        parser.error("--codex-permissions is only applicable to --provider codex")
    if args.workspace is None:
        parser.error("no worker is connected: pass --worker, set workspace in config.json or pass --workspace")
    args.workspace = args.workspace.resolve()
    args.output_dir = args.output_dir.resolve()
    if args.output_dir == args.workspace or args.workspace in args.output_dir.parents:
        parser.error("--output-dir must be outside the worker workspace")
    if args.output_dir.exists():
        parser.error("--output-dir must be a new attempt directory")


def parse_arguments() -> tuple[argparse.Namespace, dict[str, Any]]:
    """Resolve the worker, provider and workspace before anything is started."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        help="worker settings file (default: config.json, else config.example.json)")
    parser.add_argument("--worker", help="worker name from the config's workers object; never chosen automatically")
    parser.add_argument("--provider", choices=PROVIDERS)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--codex-permissions", choices=["read-only", "inherit"], default="read-only",
                        help="inherit explicitly keeps the worker's configured permissions; requires caller authorization")
    args = parser.parse_args()
    args.config = (args.config or default_config_path()).resolve()
    try:
        config = load_worker_config(args.config, args.worker)
    except (OSError, ValueError, DiscoveryError) as exc:
        parser.error(f"{exc} (config {args.config})")
    apply_config_defaults(parser, args, config)
    check_attempt_paths(parser, args)
    return args, config


def main() -> int:
    args, config = parse_arguments()
    workspace = args.workspace
    output = args.output_dir
    output.mkdir(parents=True, mode=0o700)
    receipt = {
        "provider": args.provider, "workspace": str(workspace), "kind": "read_only_discovery",
        "worker": args.worker, "config_path": str(args.config),
        "preflight_ready": False, "primary_invoked": False, "exit_code": None,
        "identity_verified": False, "git_unchanged": False, "fallback_used": False,
        "task_state": "rejected", "failure_code": None,
        "evidence_dir": str(output),
        "permissions": "Read-tools-only" if args.provider == "claude" else args.codex_permissions,
    }
    started = time.monotonic()
    before = None
    try:
        if not workspace.is_dir():
            raise DiscoveryError("WORKSPACE_MISSING")
        contexts = config["context_files"]
        for rel in contexts:
            path = (workspace / rel).resolve()
            if workspace not in path.parents or not path.is_file():
                raise DiscoveryError("CONTEXT_MISSING_OR_OUTSIDE:" + rel)
        before = snapshot(workspace)
        write_json(output / "git.before.json", before)
        binary = shutil.which(args.provider)
        if binary is None:
            raise DiscoveryError("CLI_NOT_FOUND")
        auth = [binary, "auth", "status"] if args.provider == "claude" else [binary, "login", "status"]
        receipt["preflight_argv"] = auth
        receipt["preflight_exit_code"] = run_native(auth, workspace, output, "auth", min(args.timeout, 30))
        if receipt["preflight_exit_code"] != 0:
            receipt["task_state"] = "auth-required"
            raise DiscoveryError("AUTH_PREFLIGHT_FAILED")
        receipt["preflight_ready"] = True
        properties = {
            key: {"type": "array", "items": {"type": "string"}} if key in {"context_files_read", "key_rules"}
            else {"type": "string"}
            for key in ["workspace_root", "provider", "branch", "context_files_read", "project_purpose", "key_rules"]
        }
        schema = {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}
        write_json(output / "schema.json", schema)
        prompt = (
            "This is a read-only identity discovery, not a development task. "
            "Read the following real workspace instruction files and obey their applicable rules: "
            + json.dumps(contexts) + ". Do not modify any file or Git state, run builds/servers, "
            "delegate, install anything, or use external services. Do not read credentials or private settings. "
            "Use only local file reads; no shell commands are needed. "
            "Read .git/HEAD (or the HEAD file addressed by a .git worktree pointer) to confirm the current branch. "
            "Report your actual workspace_root, provider (claude or codex), branch, context_files_read "
            "(repository-relative paths), project_purpose and at least two concrete key_rules from the files. "
            "Do not report that something was read unless you read it. Return only JSON matching this schema: "
            + json.dumps(schema)
        )
        (output / "prompt.txt").write_text(prompt, encoding="utf-8")
        if args.provider == "claude":
            argv = [binary, "-p", "--tools", "Read", "--allowedTools", "Read",
                    "--max-turns", str(config["claude_discovery_max_turns"]),
                    "--output-format", "json", "--json-schema", json.dumps(schema)]
        else:
            argv = [binary, "exec", "-C", str(workspace)]
            if args.codex_permissions == "read-only":
                argv.extend(["-s", "read-only"])
            argv.extend(["--json",
                    "--output-schema", str(output / "schema.json"), "-o", str(output / "identity.json"), "-"]
            )
        receipt["argv"] = argv
        receipt["primary_invoked"] = True
        receipt["task_state"] = "working"
        receipt["exit_code"] = run_native(argv, workspace, output, "worker", args.timeout, prompt)
        receipt["task_state"] = "failed"
        if receipt["exit_code"] != 0:
            raise DiscoveryError("TIMEOUT" if receipt["exit_code"] == 124 else "CLI_EXIT_NONZERO")
        raw = (output / "worker.stdout").read_text()
        if args.provider == "claude":
            identity = parse_claude(raw)
            write_json(output / "identity.json", identity)
        else:
            identity = parse_codex(raw, (output / "identity.json").read_text())
        validate_identity(identity, workspace, args.provider, before["branch"], contexts)
        receipt["identity_verified"] = True
        receipt["task_state"] = "discovery_verified"
    except KeyboardInterrupt:
        receipt["task_state"] = "canceled"
        receipt["failure_code"] = "USER_INTERRUPTED"
    except (DiscoveryError, OSError, ValueError, subprocess.SubprocessError) as exc:
        receipt["failure_code"] = str(exc)
    finally:
        if before is not None:
            try:
                after = snapshot(workspace)
                write_json(output / "git.after.json", after)
                receipt["git_unchanged"] = before == after
                if before != after:
                    receipt["task_state"] = "failed"
                    receipt["failure_code"] = "GIT_STATE_CHANGED"
            except (DiscoveryError, OSError, subprocess.SubprocessError) as exc:
                receipt["task_state"] = "unknown"
                receipt["failure_code"] = "POSTCHECK_FAILED:" + str(exc)
        receipt["wall_seconds"] = round(time.monotonic() - started, 3)
        write_json(output / "receipt.json", receipt)
    ok = receipt["task_state"] == "discovery_verified" and receipt["git_unchanged"]
    print(json.dumps({"ok": ok, **receipt}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
