#!/usr/bin/env python3
"""Create a redacted public snapshot without modifying the operating workspace."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import fnmatch
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
WORKER_CONFIG = ".agents/skills/worker-agent-control/config.json"
SKILLS = {
    "code-review", "harness2", "pr-review-toolkit", "rust-repack",
    "wiki-desk", "worker-agent-control",
}
TOP_FILES = {
    ".gitignore", "AGENTS.md", "README.md", "LICENSE",
    "PUBLICATION.md", "THIRD_PARTY_NOTICES.md", "scripts/prepare_public.py",
    "__llm-wiki/contract.json", "__llm-wiki/glossary.json",
}
PRIVATE_PARTS = {
    ".git", "__pycache__", "node_modules", ".cache", ".pytest_cache",
    ".venv", "target", ".ouroboros",
}
PRIVATE_NAMES = (
    ".env", ".env.*", "*.local.json", "*.local.toml", "auth.json",
    "credentials.json", "*.pem", "*.key", "id_rsa*", "id_ed25519*",
    "*.pyc", "*.pyo", ".DS_Store",
)
UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
INTERVIEW = re.compile(r"interview_\d{8}_\d{6}")
EXEC_ID = re.compile(r"(?<![A-Za-z0-9_])exec\d+")
SECRETS = {
    "private-key": re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"),
    "token": re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
        r"|sk-[A-Za-z0-9_-]{24,}|AKIA[A-Z0-9]{16})"
    ),
    "credential-assignment": re.compile(
        r'''(?i)["']?(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)'''
        r'''["']?\s*[:=]\s*["'][A-Za-z0-9_+/=.\-]{12,}["']'''
    ),
}


def allowed(path: str) -> bool:
    p = Path(path)
    if p.is_absolute() or ".." in p.parts or set(p.parts) & PRIVATE_PARTS:
        return False
    if path == WORKER_CONFIG or any(fnmatch.fnmatch(p.name, x) for x in PRIVATE_NAMES):
        return False
    if path in TOP_FILES:
        return True
    if p.parts[:2] == (".codex", "rules"):
        return p.name in {"workflow.md", "docs.md"} and len(p.parts) == 3
    if p.parts[0] == "docs":
        return p.suffix == ".md"
    if len(p.parts) > 3 and p.parts[:2] == (".agents", "skills"):
        return p.parts[2] in SKILLS and (
            p.suffix in {".md", ".mjs", ".ts", ".py", ".json", ".yaml", ".yml", ".toml"}
            or p.name in {"LICENSE", ".gitignore", ".gitattributes"}
        )
    return False


def source_files() -> dict[str, bytes]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT, capture_output=True, check=True,
    )
    paths = sorted({p.decode("utf-8") for p in result.stdout.split(b"\0") if p})
    sources = {}
    for rel in paths:
        if not allowed(rel):
            continue
        path = ROOT / rel
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError(f"Symlink source rejected: {rel}")
        if not path.is_file():
            raise ValueError(f"Missing source: {rel}")
        sources[rel] = path.read_bytes()
    for required in TOP_FILES | {".codex/rules/workflow.md", ".codex/rules/docs.md"}:
        if required not in sources:
            raise ValueError(f"Required public file missing: {required}")
    for skill in SKILLS:
        if f".agents/skills/{skill}/SKILL.md" not in sources:
            raise ValueError(f"Required public skill missing: {skill}")
    return sources


def environment_replacements() -> dict[str, str]:
    replacements = {str(ROOT): "/workspace/repack_val_agent"}
    path = ROOT / WORKER_CONFIG
    if path.is_symlink():
        raise ValueError("Symlink worker config rejected")
    if path.exists():
        workspace = Path(json.loads(path.read_text(encoding="utf-8"))["workspace"])
        if workspace.is_absolute():
            replacements[str(workspace)] = "/workspace/target_project"
        if workspace.name and workspace.name not in {"target_project", ROOT.name}:
            replacements[workspace.name] = "target_project"
    home = str(Path.home())
    if home not in {"/", "/home/user"}:
        replacements[home] = "/home/user"
    return {k: v for k, v in replacements.items() if k != v}


def redact_environment(text: str, replacements: dict[str, str]) -> tuple[str, Counter]:
    counts = Counter()
    for old, new in sorted(replacements.items(), key=lambda item: -len(item[0])):
        counts["environment"] += text.count(old)
        text = text.replace(old, new)
    # Unknown users' home paths are also generalized; retain the relative tail.
    text, n = re.subn(r"/(?:home|Users)/(?!user(?:/|\b))[^/\s`\"'<>]+", "/home/user", text)
    counts["home"] += n
    return text, counts


def redact(text: str, replacements: dict[str, str], ids: dict[str, str]) -> tuple[str, Counter]:
    text, counts = redact_environment(text, replacements)
    for label, pattern in (("session", UUID), ("interview", INTERVIEW), ("exec", EXEC_ID)):
        text, n = pattern.subn(lambda m: ids[m.group(0)], text)
        counts[label] += n
    return text, counts


def check_text(rel: str, text: str, replacements: dict[str, str]) -> None:
    for label, pattern in SECRETS.items():
        if pattern.search(text):
            raise ValueError(f"Public content rejected ({label}): {rel}")
    if any(old in text for old in replacements):
        raise ValueError(f"Unredacted environment value: {rel}")
    if rel.endswith(".md") and not rel.startswith(".agents/skills/wiki-desk/"):
        if UUID.search(text) or INTERVIEW.search(text) or EXEC_ID.search(text):
            raise ValueError(f"Unredacted session identifier: {rel}")


def wiki(output: Path, *args: str) -> dict:
    proc = subprocess.run(
        ["node", ".agents/skills/wiki-desk/src/cli.ts", *args],
        cwd=output, capture_output=True, text=True,
    )
    if proc.returncode:
        raise ValueError(f"Public wiki {' '.join(args)} failed: {proc.stdout} {proc.stderr}")
    result = json.loads(proc.stdout)
    if args[0] == "sync" and any(result.get(k) for k in (
        "protected", "invalid", "reverify", "orphans_blocked", "needs_review",
    )):
        raise ValueError("Public wiki sync requires review")
    return result


def prepare(output: Path) -> dict:
    if output.exists() or output.is_symlink():
        raise ValueError("Output must be a new directory")
    output = output.resolve()
    if output == ROOT or output in ROOT.parents:
        raise ValueError("Output must not contain the source workspace")
    if output.is_relative_to(ROOT) and not output.is_relative_to(ROOT / "temp"):
        raise ValueError("Inside the source workspace, output must be under temp/")
    sources = source_files()
    replacements = environment_replacements()
    texts = {rel: data.decode("utf-8") for rel, data in sources.items()}
    corpus = "\n".join(texts.values())
    ids = {}
    for label, pattern in (("session", UUID), ("interview", INTERVIEW), ("exec", EXEC_ID)):
        for i, value in enumerate(sorted(set(pattern.findall(corpus))), 1):
            ids[value] = f"{label}-public-{i:03d}"
    prepared = {}
    counts = Counter()
    for rel, text in texts.items():
        if not rel.startswith(".agents/skills/wiki-desk/"):
            if rel.endswith(".md"):
                text, changes = redact(text, replacements, ids)
            else:
                text, changes = redact_environment(text, replacements)
            counts.update(changes)
        check_text(rel, text, replacements)
        prepared[rel] = text
    output.mkdir(parents=True)
    for rel, text in prepared.items():
        if rel.startswith("__llm-wiki/"):
            continue
        path = output / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        path.chmod((ROOT / rel).stat().st_mode & 0o777)
    contract = output / ".public-wiki-contract.json"
    contract.write_text(prepared["__llm-wiki/contract.json"], encoding="utf-8")
    wiki(output, "init", "--contract", str(contract))
    wiki(output, "init", "--contract", str(contract), "--apply")
    contract.unlink()
    (output / "__llm-wiki/glossary.json").write_text(
        prepared["__llm-wiki/glossary.json"], encoding="utf-8",
    )
    wiki(output, "sync")  # Inspect the plan before applying it.
    wiki(output, "sync", "--apply")
    wiki(output, "check")
    # The output gets an independent Git history; never copy .git or its metadata.
    for path in (output / "__llm-wiki" / ".cache").glob("**/*"):
        if path.is_file():
            path.unlink()
    current = source_files()
    if current != sources or environment_replacements() != replacements:
        raise ValueError("Sources changed during export; do not publish this output; rerun into a new directory")
    files = sorted(p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file())
    for rel in files:
        check_text(rel, (output / rel).read_text(encoding="utf-8"), replacements)
    manifest = {
        "format_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "license": "MIT (preserved third-party notices also apply)",
        "source_history_included": False,
        "redactions": dict(counts),
        "files": files,
    }
    (output / "PUBLIC_EXPORT.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return {"output": str(output), "files": len(files) + 1, "redactions": dict(counts), "wiki": "PASS"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.output), ensure_ascii=False))
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"PUBLIC_EXPORT_FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
