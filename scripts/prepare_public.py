#!/usr/bin/env python3
"""Create a public workflow snapshot with writing guides and no task history.

The campaign skeleton (campaigns/README.md, campaigns/_template/) is included;
campaign instances are not.

Canonical rules and skills live at harness-neutral paths (rules/, skills/).
Per-harness skill discovery adapters (.agents/skills/<n>, .claude/skills/<n>)
are directory symlinks and are recreated as symlinks in the output for the base
skills. Optional skills (listed in skill-activation.yaml) ship off, without links;
the local knob skill-activation.local.yaml is never exported.
"""
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
WORKER_CONFIG = "skills/worker-agent-control/config.json"
SKILLS = {
    "anti-code-slop", "code-review", "deepeval-pack", "harness2", "pr-review-toolkit",
    "progress-monitor", "rust-repack", "skillevaluator", "wiki-desk", "worker-agent-control",
}
RULES = {"workflow.md", "docs.md", "harness.md"}
ADAPTER_DIRS = (".agents/skills", ".claude/skills")
TOP_FILES = {
    ".gitignore", "AGENTS.md", "CLAUDE.md", "README.md", "LICENSE",
    "PUBLICATION.md", "THIRD_PARTY_NOTICES.md", "scripts/prepare_public.py",
    "scripts/check_harness_adapters.py", "scripts/check_campaign.py",
    "campaigns/README.md", "campaigns/_template/campaign.json", ".claude/settings.json",
    "__llm-wiki/.gitignore", "__llm-wiki/contract.json", "__llm-wiki/glossary.json",
    "envs/.env.example", "envs/profile.example.yaml", "scripts/skill_activation.py",
    "scripts/test_skill_activation.py", "skill-activation.yaml", "repack_target/README.md",
}
PRIVATE_PARTS = {
    ".git", "__pycache__", "node_modules", ".cache", ".pytest_cache",
    ".venv", "target", ".ouroboros",
}
PRIVATE_NAMES = (
    ".env", ".env.*", "*.local.json", "*.local.toml", "*.local.yaml", "*.local.yml", "auth.json",
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
    if path == WORKER_CONFIG:
        return False
    # The key-name example is the one listed exception to the credential patterns.
    if path not in TOP_FILES and any(fnmatch.fnmatch(p.name, x) for x in PRIVATE_NAMES):
        return False
    if path in TOP_FILES:
        return True
    if p.parts[0] == "rules":
        return p.name in RULES and len(p.parts) == 2
    if p.parts[0] == "assets":
        return len(p.parts) == 2 and p.suffix in {".svg", ".md"}
    if p.parts[0] == "docs":
        return len(p.parts) == 3 and p.name == "doc_gen_guide.md"
    if p.parts[0] == "campaigns":
        # Only the skeleton is public; instances and the ACTIVE pointer stay local.
        return len(p.parts) > 2 and p.parts[1] == "_template" and p.suffix in {".json", ".jsonl", ".md"}
    if len(p.parts) > 2 and p.parts[0] == "skills":
        return p.parts[1] in SKILLS and (
            p.suffix in {".md", ".mjs", ".ts", ".py", ".json", ".yaml", ".yml", ".toml", ".rs", ".sh", ".js"}
            or p.name in {"LICENSE", ".gitignore", ".gitattributes", "uv.lock", "Cargo.lock"}
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
    for required in TOP_FILES | {f"rules/{name}" for name in RULES}:
        if required not in sources:
            raise ValueError(f"Required public file missing: {required}")
    for skill in SKILLS:
        if f"skills/{skill}/SKILL.md" not in sources:
            raise ValueError(f"Required public skill missing: {skill}")
    return sources


KNOB_ENTRY = re.compile(r"^  ([a-z0-9][a-z0-9-]*):\s*(\S*)")


def optional_skills() -> set[str]:
    """Skills listed in skill-activation.yaml ship off: the export has no discovery links for them."""
    names, inside = set(), False
    for line in (ROOT / "skill-activation.yaml").read_text(encoding="utf-8").splitlines():
        if line.startswith("skills:"):
            inside = True
        elif line and not line.startswith((" ", "#")):
            inside = False
        elif inside and (match := KNOB_ENTRY.match(line)):
            names.add(match.group(1))
            if match.group(2).split("#")[0] not in ("off", "false"):
                raise ValueError(f"skill-activation.yaml must ship every optional skill off: {match.group(1)}")
    unknown = names - SKILLS
    if unknown:
        raise ValueError(f"skill-activation.yaml lists unknown skills: {sorted(unknown)}")
    return names


def adapter_links() -> dict[str, str]:
    """Return every base skill's discovery adapter as {path: target}; optional skills get none."""
    links = {}
    optional = optional_skills()
    for base in ADAPTER_DIRS:
        for skill in sorted(s for s in SKILLS if s not in optional):
            rel = f"{base}/{skill}"
            path = ROOT / rel
            expected = f"../../skills/{skill}"
            if not path.is_symlink():
                raise ValueError(f"Skill adapter is not a symlink: {rel}")
            target = str(path.readlink())
            if target != expected or not (ROOT / "skills" / skill / "SKILL.md").is_file():
                raise ValueError(f"Skill adapter has unexpected target: {rel} -> {target}")
            links[rel] = target
    return links


def worker_workspaces() -> list[Path]:
    """Workspace paths from the local worker config: single-workspace or named workers."""
    path = ROOT / WORKER_CONFIG
    if path.is_symlink():
        raise ValueError("Symlink worker config rejected")
    if not path.exists():
        return []
    config = json.loads(path.read_text(encoding="utf-8"))
    profiles = config.get("workers")
    entries = [config] if profiles is None else list(profiles.values())
    # A single-workspace config may leave workspace null until a worker is connected.
    return [Path(entry["workspace"]) for entry in entries if entry.get("workspace")]


def environment_replacements() -> dict[str, str]:
    replacements = {str(ROOT): "/workspace/repack_val_agent"}
    workspaces = worker_workspaces()
    for index, workspace in enumerate(workspaces, 1):
        target = "/workspace/target_project" if len(workspaces) == 1 else f"/workspace/target_project_{index}"
        if workspace.is_absolute():
            replacements[str(workspace)] = target
        if workspace.name and workspace.name not in {"target_project", ROOT.name}:
            replacements[workspace.name] = Path(target).name
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
    if rel.endswith(".md") and not rel.startswith("skills/wiki-desk/"):
        if UUID.search(text) or INTERVIEW.search(text) or EXEC_ID.search(text):
            raise ValueError(f"Unredacted session identifier: {rel}")


def prepare(output: Path) -> dict:
    if output.exists() or output.is_symlink():
        raise ValueError("Output must be a new directory")
    output = output.resolve()
    if output == ROOT or output in ROOT.parents:
        raise ValueError("Output must not contain the source workspace")
    if output.is_relative_to(ROOT) and not output.is_relative_to(ROOT / "temp"):
        raise ValueError("Inside the source workspace, output must be under temp/")
    sources = source_files()
    links = adapter_links()
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
        if not rel.startswith("skills/wiki-desk/"):
            if rel.endswith(".md"):
                text, changes = redact(text, replacements, ids)
            else:
                text, changes = redact_environment(text, replacements)
            counts.update(changes)
        check_text(rel, text, replacements)
        prepared[rel] = text
    output.mkdir(parents=True)
    for rel, text in prepared.items():
        path = output / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        path.chmod((ROOT / rel).stat().st_mode & 0o777)
    for rel, target in links.items():
        path = output / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target, target_is_directory=True)
    # The output gets an independent Git history; never copy .git or its metadata.
    current = source_files()
    if current != sources or adapter_links() != links or environment_replacements() != replacements:
        raise ValueError("Sources changed during export; do not publish this output; rerun into a new directory")
    files = sorted(
        p.relative_to(output).as_posix() for p in output.rglob("*")
        if p.is_file() and not any(q.is_symlink() for q in (p, *p.parents))
    )
    for rel in files:
        check_text(rel, (output / rel).read_text(encoding="utf-8"), replacements)
    manifest = {
        "format_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "license": "MIT (preserved third-party notices also apply)",
        "source_history_included": False,
        "generated_documents_included": False,
        "wiki_runtime_included": False,
        "redactions": dict(counts),
        "files": files,
        "symlinks": links,
    }
    (output / "PUBLIC_EXPORT.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return {"output": str(output), "files": len(files) + 1, "redactions": dict(counts), "wiki_runtime": "EXCLUDED"}


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
