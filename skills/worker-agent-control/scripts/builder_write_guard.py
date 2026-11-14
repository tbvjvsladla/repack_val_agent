"""Run one code-builder command with private TMPDIR and explicit write directories.

Linux Landlock ABI >= 3, aarch64/x86_64. This is not a general filesystem or
network sandbox. Read/execute/network remain unrestricted by this helper.
no_new_privs also prevents setuid elevation in descendants.
"""
from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path
import platform
import stat
import sys


class Ruleset(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class Beneath(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


WRITE_FILE = 1 << 1
TRUNCATE = 1 << 14
WRITE_RIGHTS = WRITE_FILE | sum(1 << bit for bit in range(4, 15))


def restrict_builder_writes(private_tmp: Path, write_dirs: list[Path]) -> int:
    if sys.platform != "linux" or platform.machine() not in {"aarch64", "x86_64"}:
        raise RuntimeError("Linux aarch64/x86_64 is required")
    if private_tmp.is_symlink():
        raise RuntimeError("private TMPDIR must not be a symlink")
    private_tmp = private_tmp.resolve(strict=True)
    info = private_tmp.stat()
    if private_tmp == Path("/tmp") or not stat.S_ISDIR(info.st_mode):
        raise RuntimeError("a separate private TMPDIR directory is required")
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise RuntimeError("private TMPDIR must be caller-owned with mode 0700")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long

    def call(number: int, *arguments: object) -> int:
        value = libc.syscall(number, *arguments)
        if value < 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), "Landlock syscall " + str(number))
        return int(value)

    abi = call(444, 0, 0, 1)
    if abi < 3:
        raise RuntimeError("Landlock ABI >= 3 is required, observed " + str(abi))
    rules = Ruleset(WRITE_RIGHTS)
    rules_fd = call(444, ctypes.byref(rules), ctypes.sizeof(rules), 0)

    def allow(path: Path) -> None:
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
        try:
            directory = stat.S_ISDIR(os.fstat(fd).st_mode)
            access = WRITE_RIGHTS if directory else WRITE_FILE | TRUNCATE
            rule = Beneath(access, fd)
            call(445, rules_fd, 1, ctypes.byref(rule), 0)
        finally:
            os.close(fd)

    try:
        # Shell redirections and normal CLI probes need the standard sink.
        # Grant this leaf only, rather than the whole device directory.
        allow(Path("/dev/null"))
        # Only the explicit builder/source/check/runtime record subtrees are writable.
        # Landlock resolves symlink targets, so a /tmp symlink into an unrelated
        # home/project path does not receive a write grant either.
        for path in [private_tmp, *write_dirs]:
            resolved = path.resolve(strict=True)
            if resolved == Path("/") or resolved == Path("/tmp") or not resolved.is_dir():
                raise RuntimeError("write grants must name dedicated directories")
            allow(resolved)
        if libc.prctl(38, 1, 0, 0, 0) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), "PR_SET_NO_NEW_PRIVS")
        call(446, rules_fd, 0)
    finally:
        os.close(rules_fd)
    return abi


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tmpdir", type=Path, required=True)
    parser.add_argument("--write-dir", type=Path, action="append", default=[])
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command after -- is required")
    try:
        restrict_builder_writes(args.tmpdir, args.write_dir)
    except (OSError, RuntimeError) as error:
        print("tmp_guard: command NOT_RUN: " + str(error), file=sys.stderr)
        return 77
    environment = dict(os.environ, TMPDIR=str(args.tmpdir.resolve()), GIT_OPTIONAL_LOCKS="0")
    os.execvpe(command[0], command, environment)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
