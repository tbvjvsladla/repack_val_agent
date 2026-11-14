"""Prepare per-invocation Claude code-builder settings, without editing settings scopes."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys


def check_external_hardlinks(roots: list[Path]) -> int:
    files = {}
    counts = Counter()
    for root in roots:
        for directory, _, names in os.walk(root, followlinks=False):
            for name in names:
                path = Path(directory) / name
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode):
                    continue
                if path in files:
                    continue
                files[path] = info
                counts[(info.st_dev, info.st_ino)] += 1
    for path, info in files.items():
        if info.st_nlink != counts[(info.st_dev, info.st_ino)]:
            raise ValueError("external hardlink in write roots: " + str(path))
    return len(files)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--write-dir", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--executable", default="claude")
    args = parser.parse_args()
    workspace = args.workspace.resolve(strict=True)
    roots = list(dict.fromkeys(p.resolve(strict=True) for p in args.write_dir))
    out = args.output_dir.absolute()
    helpers = Path(__file__).resolve().parent
    guard = helpers / "builder_write_guard.py"
    hook = helpers / "claude_builder_hook.py"
    for root in roots:
        if not root.is_dir() or root in {Path('/'), Path('/tmp'), Path.home(), workspace}:
            raise ValueError("a dedicated source/check directory is required")
        if out.resolve().is_relative_to(root) or helpers.is_relative_to(root):
            raise ValueError("settings, ledger and helpers must be outside write roots")
    count = check_external_hardlinks(roots)
    version = subprocess.run([args.executable, '--version'], cwd=workspace,
                             capture_output=True, text=True, timeout=15, check=True)
    match = re.match(r'(\d+)\.(\d+)\.(\d+)\b', version.stdout)
    if not match or tuple(map(int, match.groups())) < (2, 1, 295):
        raise ValueError('Claude Code >= 2.1.295 is required for onFailure:block')
    out.mkdir(parents=True, exist_ok=False, mode=0o700)
    private = out / 'private-tmp'
    private.mkdir(mode=0o700)
    preflight = [sys.executable, str(guard), '--tmpdir', str(private)]
    for root in roots:
        preflight += ['--write-dir', str(root)]
    preflight += ['--', '/bin/true']
    p = subprocess.run(preflight, cwd=workspace, capture_output=True, timeout=15)
    (out/'preflight.stdout').write_bytes(p.stdout)
    (out/'preflight.stderr').write_bytes(p.stderr)
    receipt = dict(version=version.stdout.strip(), files_checked=count,
                   roots=list(map(str, roots)), preflight_argv=preflight,
                   preflight_exit=p.returncode, state='NOT_RUN')
    if p.returncode:
        (out/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
        return 77
    config = dict(workspace=str(workspace), write_dirs=list(map(str, roots)),
                  runtime_write_dirs=[], private_tmp=str(private), guard=str(guard),
                  ledger=str(out/'ledger.jsonl'))
    (out/'guard.json').write_text(json.dumps(config, indent=2)+'\n')
    command = shlex.join([sys.executable, str(hook), '--config', str(out/'guard.json')])
    settings = {'hooks': {'PreToolUse': [{'matcher': '.*', 'hooks': [
        {'type': 'command', 'command': command, 'timeout': 10, 'onFailure': 'block'}]}]}}
    (out/'settings.json').write_text(json.dumps(settings, indent=2)+'\n')
    receipt['state'] = 'READY_FOR_GUARDED_INVOCATION_NOT_BUSINESS_ACCEPTANCE'
    (out/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(out/'settings.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
