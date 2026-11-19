"""Deterministic slop scanner: language lexers, rule application, protected comments.

Standard library only. The same input always produces the same output.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import re
import textwrap
import tokenize
from pathlib import Path

RULES_PATH = Path(__file__).resolve().with_name("rules.json")

# Build outputs and tool caches. Never hashed, never scanned.
HASH_EXCLUDE_DIRS = frozenset({
    ".git", "target", "node_modules", "__pycache__", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
})
HASH_EXCLUDE_SUFFIXES = (".pyc",)
# Hashed (scope changes are still seen) but not scanned for slop.
SCAN_EXCLUDE_DIRS = frozenset({"vendor", "third_party", "dist", "build"})
LOCK_FILES = frozenset({"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Cargo.lock", "poetry.lock", "uv.lock"})
MAX_SCAN_BYTES = 2 * 1024 * 1024

EXTENSIONS = {
    ".rs": "rust", ".py": "python",
    ".js": "js", ".mjs": "js", ".cjs": "js", ".jsx": "js",
    ".ts": "js", ".tsx": "js", ".mts": "js", ".cts": "js",
    ".sh": "shell", ".bash": "shell",
    ".md": "prose", ".markdown": "prose", ".txt": "prose", ".rst": "prose",
}
CODE_LANGUAGES = ("rust", "python", "js", "shell")
SHELL_SHEBANG = re.compile(r"^#!.*\b(ba|z|k|da)?sh\b")

SIGNATURE = {
    "rust": re.compile(r"^\s*(pub(\([^)]*\))?\s+)?(async\s+)?(unsafe\s+)?(const\s+)?(fn|impl|struct|enum|trait|mod)\b"),
    "python": re.compile(r"^\s*(async\s+)?(def|class)\s"),
    "js": re.compile(r"^\s*(export\s+)?(default\s+)?(async\s+)?(function|class)\b"
                     r"|^\s*(export\s+)?(const|let|var)\s+\w+\s*=\s*(async\s*)?(\(|function)"),
    "shell": re.compile(r"^\s*(function\s+[\w-]+|[\w-]+\s*\(\s*\))"),
    "prose": re.compile(r"^\s*#{1,6}\s"),
}

PROTECTED_PATTERNS = (
    ("legal", re.compile(r"SPDX-License-Identifier|(?i:copyright)|©|Licensed under|All rights reserved")),
    ("issue_rfc", re.compile(r"https?://\S+/(issues|pull|pulls|merge_requests)/\d+|rfc-editor\.org|\bRFC\s?\d{3,5}\b")),
    ("allowed_suppression", re.compile(r"prettier-ignore")),
)

SKIP = object()


# ---------------------------------------------------------------- utilities

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_rules(path: Path = RULES_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    rules = data["rules"]
    for rule in rules:
        if "pattern" in rule:
            rule["_re"] = {lang: re.compile(rule["pattern"]) for lang in rule["languages"]}
        elif "patterns" in rule:
            rule["_re"] = {lang: re.compile(p) for lang, p in rule["patterns"].items()}
    return rules


def constraint_regex(rules: list[dict]) -> re.Pattern:
    for rule in rules:
        if rule["id"] == "ANY-CONSTRAINT-MARKER":
            return re.compile(rule["pattern"])
    raise KeyError("ANY-CONSTRAINT-MARKER")


def excluded_rel(rel: str) -> bool:
    parts = Path(rel).parts
    return bool(set(parts[:-1]) & HASH_EXCLUDE_DIRS) or (parts and parts[-1] in HASH_EXCLUDE_DIRS) \
        or rel.endswith(HASH_EXCLUDE_SUFFIXES)


def walk_files(root: Path) -> list[str]:
    """All target-relative file paths except the hash exclusion list (relative parts only)."""
    out = []
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        dirnames[:] = sorted(d for d in dirnames if d not in HASH_EXCLUDE_DIRS)
        for name in sorted(filenames):
            rel = (rel_dir / name).as_posix()
            if rel.endswith(HASH_EXCLUDE_SUFFIXES):
                continue
            full = Path(dirpath) / name
            if full.is_symlink() or not full.is_file():
                continue
            out.append(rel)
    return sorted(out)


def hash_tree(root: Path) -> dict[str, str]:
    return {rel: sha256_bytes((Path(root) / rel).read_bytes()) for rel in walk_files(root)}


def under(rel: str, prefixes) -> bool:
    for prefix in prefixes:
        p = prefix.strip("/")
        if p in ("", "."):
            return True
        if rel == p or rel.startswith(p + "/"):
            return True
    return False


NOT_PROSE_TXT = re.compile(r"^(requirements.*|constraints.*|CMakeLists|LICENSE.*|NOTICE.*|COPYING.*)\.txt$", re.I)


def detect_language(rel: str, data: bytes) -> str | None:
    suffix = Path(rel).suffix.lower()
    if suffix == ".txt" and NOT_PROSE_TXT.match(Path(rel).name):
        return None
    if suffix in EXTENSIONS:
        return EXTENSIONS[suffix]
    if suffix == "":
        first = data.split(b"\n", 1)[0].decode("utf-8", "replace")
        if SHELL_SHEBANG.match(first):
            return "shell"
    return None


def scan_exclusion(rel: str, data: bytes) -> str | None:
    parts = Path(rel).parts
    if set(parts[:-1]) & SCAN_EXCLUDE_DIRS:
        return "vendor_or_build_dir"
    if parts[-1] in LOCK_FILES or parts[-1].endswith(".lock"):
        return "lock_file"
    if len(data) > MAX_SCAN_BYTES:
        return "too_large"
    if b"\0" in data[:8192]:
        return "binary"
    head = b"\n".join(data.split(b"\n", 5)[:5])
    if b"@generated" in head or b"DO NOT EDIT" in head:
        return "generated"
    return None


# ------------------------------------------------------------------ lexers

class Lexed:
    """Per-line view of a source file.

    lines: original lines. code: the same lines with comments and string/regex
    contents blanked. comments: (line_no, text, kind) with kind in
    {"line", "block", "doc", "inner_doc"}; text excludes the comment markers.
    """

    def __init__(self, lines: list[str], code: list[str], comments: list[tuple[int, str, str]]):
        self.lines = lines
        self.code = code
        self.comments = comments
        self.comment_lines = {c[0] for c in comments}

    def comment_only(self, idx: int) -> bool:
        return (idx + 1) in self.comment_lines and not self.code[idx].strip()


def _blank(chars: list[str], start: int, end: int) -> None:
    for k in range(start, end):
        if chars[k] != "\n":
            chars[k] = " "


RUST_CHAR = re.compile(r"'(?:\\(?:x[0-9a-fA-F]{2}|u\{[0-9a-fA-F]{1,6}\}|.)|[^\\'\n])'")
JS_REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^")
JS_REGEX_KEYWORDS = {"return", "typeof", "case", "do", "else", "in", "of", "yield", "await", "void", "delete", "new"}


def lex_clike(text: str, lang: str) -> Lexed:
    rust = lang == "rust"
    chars = list(text)
    n = len(text)
    comments: list[tuple[int, str, str]] = []
    i = 0
    line = 1
    last_sig = ""      # last significant code character (JS regex detection)
    last_word = ""     # last identifier (JS regex detection)

    def add_block(start_line: int, body: str, kind: str) -> None:
        for offset, part in enumerate(body.split("\n")):
            stripped = part.strip()
            if stripped.startswith("*"):
                stripped = stripped[1:].strip()
            comments.append((start_line + offset, stripped, kind))

    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        two = text[i:i + 2]
        if two == "//":
            j = text.find("\n", i)
            j = n if j < 0 else j
            body = text[i + 2:j]
            kind = "line"
            if rust and body.startswith("!"):
                kind, body = "inner_doc", body[1:]
            elif rust and body.startswith("/") and not body.startswith("//"):
                kind, body = "doc", body[1:]
            comments.append((line, body.strip(), kind))
            _blank(chars, i, j)
            i = j
            continue
        if two == "/*":
            start_line = line
            depth = 1
            j = i + 2
            while j < n and depth:
                if rust and text.startswith("/*", j):
                    depth += 1
                    j += 2
                elif text.startswith("*/", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            body = text[i + 2:j - 2 if depth == 0 else j]
            kind = "block"
            if body.startswith("*") and not body.startswith("*/") and body != "*":
                kind, body = "doc", body[1:]
            elif rust and body.startswith("!"):
                kind, body = "inner_doc", body[1:]
            add_block(start_line, body, kind)
            line += text.count("\n", i, j)
            _blank(chars, i, j)
            i = j
            continue
        if rust and (c == "r" or (c == "b" and text[i + 1:i + 2] == "r")):
            m = re.compile(r'b?r(#*)"').match(text, i)
            if m and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")):
                hashes = m.group(1)
                end = text.find('"' + hashes, m.end())
                end = n if end < 0 else end + 1 + len(hashes)
                line += text.count("\n", i, end)
                _blank(chars, m.end(), max(m.end(), end - 1 - len(hashes)))
                i = end
                last_sig = '"'
                continue
        if c == '"' or (c == "'" and not rust) or (c == "`" and not rust):
            quote = c
            j = i + 1
            while j < n:
                ch = text[j]
                if ch == "\\":
                    j += 2
                    continue
                if quote == "`" and text.startswith("${", j):
                    depth = 1
                    k = j + 2
                    while k < n and depth:
                        if text[k] == "{":
                            depth += 1
                        elif text[k] == "}":
                            depth -= 1
                        k += 1
                    _blank(chars, i + 1, j)
                    i = j = k
                    continue
                if ch == quote:
                    break
                if ch == "\n" and not (quote == "`" or (rust and quote == '"')):
                    break
                j += 1
            end = j + 1 if j < n and text[j] == quote else j
            line += text.count("\n", i, end)
            _blank(chars, i + 1, min(j, n))
            i = end
            last_sig = quote
            continue
        if c == "'" and rust:
            m = RUST_CHAR.match(text, i)
            if m:
                _blank(chars, i + 1, m.end() - 1)
                i = m.end()
                last_sig = "'"
                continue
            i += 1  # lifetime
            continue
        if c == "/" and not rust:
            if last_sig == "" or last_sig in JS_REGEX_PREV or last_word in JS_REGEX_KEYWORDS:
                j = i + 1
                in_class = False
                while j < n and text[j] != "\n":
                    ch = text[j]
                    if ch == "\\":
                        j += 2
                        continue
                    if ch == "[":
                        in_class = True
                    elif ch == "]":
                        in_class = False
                    elif ch == "/" and not in_class:
                        break
                    j += 1
                if j < n and text[j] == "/":
                    _blank(chars, i + 1, j)
                    i = j + 1
                    while i < n and text[i].isalpha():
                        i += 1
                    last_sig, last_word = "/", ""
                    continue
        if not c.isspace():
            if c.isalnum() or c == "_" or c == "$":
                k = i
                while k < n and (text[k].isalnum() or text[k] in "_$"):
                    k += 1
                last_word = text[i:k]
                last_sig = "a"
                i = k
                continue
            last_sig = c
            last_word = ""
        i += 1
    masked = "".join(chars)
    return Lexed(text.split("\n"), masked.split("\n"), sorted(comments))


def lex_python(text: str) -> Lexed | None:
    lines = text.split("\n")
    chars = [list(l) for l in lines]
    comments = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None
    blank_types = {tokenize.STRING, tokenize.COMMENT}
    fstring_middle = getattr(tokenize, "FSTRING_MIDDLE", None)
    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            comments.append((tok.start[0], tok.string[1:].strip(), "line"))
        if tok.type in blank_types or (fstring_middle is not None and tok.type == fstring_middle):
            (sl, sc), (el, ec) = tok.start, tok.end
            for ln in range(sl, el + 1):
                row = chars[ln - 1]
                a = sc if ln == sl else 0
                b = ec if ln == el else len(row)
                if tok.type == tokenize.STRING and ln == sl:
                    a = min(len(row), a + 1)  # keep the opening quote visible
                for k in range(a, min(b, len(row))):
                    row[k] = " "
    return Lexed(lines, ["".join(r) for r in chars], sorted(comments))


HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")


def lex_shell(text: str) -> Lexed:
    lines = text.split("\n")
    code = []
    comments = []
    stack: list[str] = []          # "'", '"', "$(" contexts carried across lines
    heredoc: tuple[str, bool] | None = None
    for idx, raw in enumerate(lines):
        if heredoc is not None:
            word, strip_tabs = heredoc
            check = raw.lstrip("\t") if strip_tabs else raw
            code.append(" " * len(raw))
            if check == word:
                heredoc = None
            continue
        out = list(raw)
        i = 0
        n = len(raw)
        while i < n:
            ch = raw[i]
            top = stack[-1] if stack else None
            if top == "'":
                if ch == "'":
                    stack.pop()
                else:
                    out[i] = " "
                i += 1
                continue
            if top == '"':
                if ch == "\\":
                    out[i] = " "
                    if i + 1 < n:
                        out[i + 1] = " "
                    i += 2
                    continue
                if raw.startswith("$(", i):
                    stack.append("$(")
                    i += 2
                    continue
                if ch == '"':
                    stack.pop()
                else:
                    out[i] = " "
                i += 1
                continue
            # default or "$(" context
            if ch == "\\":
                i += 2
                continue
            if ch == "'":
                stack.append("'")
                i += 1
                continue
            if ch == '"':
                stack.append('"')
                i += 1
                continue
            if raw.startswith("$(", i):
                stack.append("$(")
                i += 2
                continue
            if ch == ")" and top == "$(":
                stack.pop()
                i += 1
                continue
            if ch == "#" and (i == 0 or raw[i - 1] in " \t;|&(") :
                comments.append((idx + 1, raw[i + 1:].strip(), "line"))
                for k in range(i, n):
                    out[k] = " "
                break
            i += 1
        masked = "".join(out)
        code.append(masked)
        for m in HEREDOC.finditer(raw):
            before = masked[:m.start()]
            in_arithmetic = before.count("((") > before.count("))")
            if masked[m.start():m.start() + 2] == "<<" and not in_arithmetic:
                heredoc = (m.group(3), m.group(1) == "-")
                break
    return Lexed(lines, code, sorted(comments))


def lex(text: str, lang: str) -> Lexed | None:
    if lang in ("rust", "js"):
        return lex_clike(text, lang)
    if lang == "python":
        return lex_python(text)
    if lang == "shell":
        return lex_shell(text)
    return None


def prose_lines(text: str) -> list[str | None]:
    """Prose text with fenced code blocks dropped (None) and inline code blanked."""
    out: list[str | None] = []
    fence = None
    for raw in text.split("\n"):
        stripped = raw.lstrip()
        marker = stripped[:3]
        if fence is None and marker in ("```", "~~~"):
            fence = marker
            out.append(None)
            continue
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            out.append(None)
            continue
        out.append(re.sub(r"`[^`]*`", lambda m: " " * len(m.group(0)), raw))
    return out


# ---------------------------------------------------------------- builtins

PY_CODE_STATEMENTS = (ast.Assign, ast.AugAssign, ast.Return, ast.If, ast.For, ast.AsyncFor, ast.While,
                      ast.With, ast.AsyncWith, ast.Try, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                      ast.Import, ast.ImportFrom, ast.Raise, ast.Delete, ast.Assert)


def _py_meaningful(tree: ast.Module) -> bool:
    """True when the block holds a statement only code would contain (prose that happens to parse does not)."""
    for stmt in tree.body:
        if isinstance(stmt, PY_CODE_STATEMENTS):
            return True
        if isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            return True
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, (ast.Call, ast.Await)):
            return True
    return False


def _py_parse_block(texts: list[str]) -> bool:
    src = textwrap.dedent("\n".join(texts))
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return False
    return _py_meaningful(tree)


def _comment_runs(lx: Lexed, accept) -> list[list[tuple[int, str]]]:
    """Runs of consecutive comment-only lines whose comment passes accept()."""
    runs, current, prev = [], [], None
    by_line = {}
    for ln, body, kind in lx.comments:
        by_line.setdefault(ln, (body, kind))
    for ln in sorted(by_line):
        body, kind = by_line[ln]
        if not lx.comment_only(ln - 1) or not accept(kind):
            if current:
                runs.append(current)
            current, prev = [], None
            continue
        if prev is not None and ln != prev + 1:
            runs.append(current)
            current = []
        current.append((ln, body))
        prev = ln
    if current:
        runs.append(current)
    return runs


def check_py_commented_code(text, lang, lx):
    hits = []
    raw_lines = lx.lines
    for run in _comment_runs(lx, lambda kind: kind == "line"):
        bodies = []
        for ln, _ in run:
            raw = raw_lines[ln - 1].lstrip()
            body = raw[1:]
            if body.startswith(" "):
                body = body[1:]
            bodies.append(body)
        i = 0
        while i < len(run) - 1:
            found = 0
            for j in range(min(len(run), i + 40), i + 1, -1):
                if _py_parse_block(bodies[i:j]):
                    found = j
                    break
            if found:
                hits.append(run[i][0])
                i = found
            else:
                i += 1
    return hits


CLIKE_CODE_START = re.compile(
    r"^(let|const|var|fn|pub|return|if|else|for|while|match|use|import|export|function|class|await|this|self|try|catch)\b"
    r"|^[A-Za-z_$][\w$]*(::[\w$]+)*(\.[\w$]+)*\s*(\(|=[^=]|\+=|-=|\[|\.|!)"
    r"|^\}"
)


def check_clike_commented_code(text, lang, lx):
    hits = []
    for run in _comment_runs(lx, lambda kind: kind == "line"):
        streak = []
        for ln, body in run + [(None, "")]:
            ok = ln is not None and body.rstrip().endswith((";", "{", "}")) and CLIKE_CODE_START.search(body.strip())
            if ok:
                streak.append(ln)
                continue
            if len(streak) >= 2:
                hits.append(streak[0])
            streak = []
    return hits


BANNER = re.compile(r"([=\-*#/~])\1{9,}")
TABLE_BORDER = re.compile(r"^[\s#/*!]*[+|][-=+|:\s]*[+|][\s*/]*$")
LEGAL = re.compile(r"SPDX-License-Identifier|(?i:copyright)|\u00a9|Licensed under|All rights reserved")


def _legal_block_lines(lx: Lexed) -> set[int]:
    """Comment-only lines in a contiguous comment block that carries a legal notice."""
    comment_only = sorted(ln for ln in lx.comment_lines if lx.comment_only(ln - 1))
    blocks, current = [], []
    for ln in comment_only:
        if current and ln != current[-1] + 1:
            blocks.append(current)
            current = []
        current.append(ln)
    if current:
        blocks.append(current)
    legal = {ln for ln, body, _ in lx.comments if LEGAL.search(body)}
    first_code = next((i + 1 for i, line in enumerate(lx.code) if line.strip()), len(lx.lines) + 1)
    head = [b for b in blocks if b[0] < first_code]
    return {ln for block in head if legal & set(block) for ln in block}


def _worded_doc_lines(lx: Lexed) -> set[int]:
    """Doc-comment lines in a contiguous doc block that contains any text (e.g. a heading underline)."""
    docs = sorted((ln, body) for ln, body, kind in lx.comments if kind in ("doc", "inner_doc"))
    blocks, current = [], []
    for ln, body in docs:
        if current and ln > current[-1][0] + 1:
            blocks.append(current)
            current = []
        current.append((ln, body))
    if current:
        blocks.append(current)
    out = set()
    for block in blocks:
        if any(re.search(r"[^\s=\-*#/~]", body) for _, body in block):
            out.update(ln for ln, _ in block)
    return out


def check_banner(text, lang, lx):
    hits = []
    legal_block = _legal_block_lines(lx)
    worded_docs = _worded_doc_lines(lx)
    for ln, body, kind in lx.comments:
        raw = lx.lines[ln - 1]
        if ln == 1 and raw.startswith("#!"):
            continue
        if ln in legal_block:
            continue
        if kind in ("doc", "inner_doc") and ln in worded_docs:
            continue
        target = raw.strip() if lx.comment_only(ln - 1) else body
        if TABLE_BORDER.match(target):
            continue
        if BANNER.search(target):
            hits.append(ln)
    return sorted(set(hits))


BROAD = {"Exception", "BaseException"}


def _handler_names(node) -> set[str]:
    if node is None:
        return {"<bare>"}
    items = node.elts if isinstance(node, ast.Tuple) else [node]
    names = set()
    for item in items:
        if isinstance(item, ast.Name):
            names.add(item.id)
        elif isinstance(item, ast.Attribute):
            names.add(item.attr)
        else:
            names.add("<expr>")
    return names


def _only(body, kinds) -> bool:
    for stmt in body:
        if isinstance(stmt, ast.Pass) and "pass" in kinds:
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and "const" in kinds:
            continue
        if isinstance(stmt, ast.Continue) and "continue" in kinds:
            continue
        return False
    return True


def _py_tree(text):
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError):
        return SKIP


def check_py_broad_swallow(text, lang, lx):
    tree = _py_tree(text)
    if tree is SKIP:
        return []
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            names = _handler_names(node.type)
            broad = "<bare>" in names or bool(names & BROAD)
            if broad and _only(node.body, {"pass", "const"}):
                hits.append(node.lineno)
    return hits


def check_py_ignored_exception(text, lang, lx):
    tree = _py_tree(text)
    if tree is SKIP:
        return []
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            names = _handler_names(node.type)
            broad = "<bare>" in names or bool(names & BROAD)
            swallows = _only(node.body, {"pass", "const", "continue"})
            returns_constant = broad and len(node.body) == 1 and isinstance(node.body[0], ast.Return) \
                and (node.body[0].value is None or isinstance(node.body[0].value, ast.Constant))
            if (swallows and not (broad and _only(node.body, {"pass", "const"}))) or returns_constant:
                hits.append(node.lineno)
    return hits


JS_EMPTY_CATCH = re.compile(r"\bcatch\s*(\([^()]*\))?\s*\{\s*\}|\.catch\(\s*(\([^()]*\)|\w+)\s*=>\s*(\{\s*\}|undefined|null)\s*\)")


def check_js_empty_catch(text, lang, lx):
    joined = "\n".join(lx.code)
    hits = []
    for m in JS_EMPTY_CATCH.finditer(joined):
        hits.append(joined.count("\n", 0, m.start()) + 1)
    return hits


BOLD_LABEL = re.compile(r"^\s*(?:[-*+]\s+)?\*\*([^*:]+):\*\*\s*([^\s.,;:]+)")


def check_prose_bold_echo(text, lang, lines):
    hits = []
    for idx, line in enumerate(lines):
        if line is None:
            continue
        m = BOLD_LABEL.match(line)
        if m and m.group(1).split()[0].lower() == m.group(2).lower():
            hits.append(idx + 1)
    return hits


def _is_emoji(ch: str) -> bool:
    cp = ord(ch)
    return 0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF or 0x2B00 <= cp <= 0x2BFF


def check_prose_emoji(text, lang, lines):
    hits = []
    for idx, line in enumerate(lines):
        if line is None:
            continue
        if re.match(r"^\s*(#{1,6}\s|[-*+]\s|\d+[.)]\s)", line) and any(_is_emoji(ch) for ch in line):
            hits.append(idx + 1)
    return hits


BUILTINS = {
    "py_commented_code": check_py_commented_code,
    "clike_commented_code": check_clike_commented_code,
    "banner": check_banner,
    "py_broad_swallow": check_py_broad_swallow,
    "py_ignored_exception": check_py_ignored_exception,
    "js_empty_catch": check_js_empty_catch,
    "prose_bold_echo": check_prose_bold_echo,
    "prose_emoji": check_prose_emoji,
}


# ------------------------------------------------------------------- scanning

def _rule_hits(rule: dict, lang: str, text: str, lx: Lexed | None, plines) -> list[int]:
    if lang not in rule["languages"]:
        return []
    where = rule["where"]
    if "builtin" in rule:
        if where == "prose":
            return BUILTINS[rule["builtin"]](text, lang, plines)
        if lx is None:
            return []
        return BUILTINS[rule["builtin"]](text, lang, lx)
    regex = rule["_re"].get(lang)
    if regex is None:
        return []
    hits = []
    if where == "comment":
        if lx is None:
            return []
        for ln, body, kind in lx.comments:
            if regex.search(body):
                hits.append(ln)
    elif where == "code":
        if lx is None:
            return []
        for idx, line in enumerate(lx.code):
            if regex.search(line):
                hits.append(idx + 1)
    elif where == "prose":
        for idx, line in enumerate(plines):
            if line is not None and regex.search(line):
                hits.append(idx + 1)
    return sorted(set(hits))


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _context(lang: str, lines: list[str], lx: Lexed | None, ln: int) -> str:
    """Hash of the signal line, the next code line and the enclosing item's signature.

    The enclosing item is found by climbing the indentation parents of the signal
    line until a signature line; sibling items and lines above do not take part.
    """
    idx = ln - 1
    cur = lines[idx].strip()
    nxt = ""
    for k in range(idx + 1, len(lines)):
        if not lines[k].strip():
            continue
        if lx is not None and lx.comment_only(k):
            continue
        nxt = lines[k].strip()
        break
    sig = ""
    pattern = SIGNATURE.get(lang)
    level = _indent(lines[idx])
    if pattern is not None and level > 0:
        for k in range(idx - 1, -1, -1):
            if not lines[k].strip() or (lx is not None and lx.comment_only(k)):
                continue
            indent = _indent(lines[k])
            if indent >= level:
                continue
            if pattern.match(lines[k]):
                sig = lines[k].strip()
                break
            level = indent  # a non-signature parent block (if, for, main guard): keep climbing from it
            if level == 0:
                break
    digest = hashlib.sha1("\x1f".join((cur, nxt, sig)).encode("utf-8")).hexdigest()
    return digest[:10]


def scan_text(text: str, lang: str, rules: list[dict], rel: str = "<text>") -> tuple[list[dict], str | None]:
    """Signals for one file's text. Returns (signals, parse_error)."""
    lines = text.split("\n")
    lx = None
    plines = None
    error = None
    if lang == "prose":
        plines = prose_lines(text)
    else:
        lx = lex(text, lang)
        if lx is None:
            error = "tokenize_failed"
        if lang == "python" and _py_tree(text) is SKIP:
            error = error or "syntax_error"
    raw = []
    for rule in rules:
        for ln in _rule_hits(rule, lang, text, lx, plines):
            raw.append((ln, rule))
    signals = []
    counters: dict[tuple[str, str], int] = {}
    for ln, rule in sorted(raw, key=lambda item: (item[0], item[1]["id"])):
        ctx = _context(lang, lines, lx, ln)
        key = (rule["id"], ctx)
        k = counters.get(key, 0)
        counters[key] = k + 1
        signals.append({
            "id": f"{rule['id']}:{rel}:{ctx}:{k}",
            "rule": rule["id"],
            "file": rel,
            "line": ln,
            "text": lines[ln - 1].strip()[:200],
            "grade": rule["grade"],
            "fix_type": rule["fix_type"],
            "language": lang,
            "context": ctx,
        })
    return signals, error


def _in_scope(rel: str, scope) -> bool:
    return scope is None or under(rel, scope)


def scan_tree(root: Path, rules: list[dict], scope=None, only=None, force=None) -> dict:
    """Scan files under scope. `force` maps paths to the language they had before an edit;
    those files are scanned as that language even if they now look generated or excluded."""
    root = Path(root)
    force = force or {}
    checked, unchecked, excluded, errors, signals = {}, [], {}, [], []
    for rel in walk_files(root):
        if not _in_scope(rel, scope) or (only is not None and rel not in only):
            continue
        data = (root / rel).read_bytes()
        reason = None if rel in force else scan_exclusion(rel, data)
        if reason:
            excluded[rel] = reason
            continue
        lang = force.get(rel) or detect_language(rel, data)
        if lang is None:
            unchecked.append(rel)
            continue
        text = data.decode("utf-8", "replace")
        found, error = scan_text(text, lang, rules, rel)
        checked[rel] = lang
        if error:
            errors.append({"file": rel, "error": error})
        signals.extend(found)
    signals.sort(key=lambda s: (s["file"], s["line"], s["rule"], s["id"]))
    return {
        "scope": sorted(scope) if scope is not None else ["."],
        "files": {"checked": checked, "unchecked": unchecked, "excluded": excluded},
        "parse_errors": errors,
        "signals": signals,
    }


# ------------------------------------------------------- protected comments

def _py_public_docstrings(text: str) -> list[tuple[int, str]]:
    tree = _py_tree(text)
    if tree is SKIP:
        return []
    out = []

    def doc_of(node):
        body = getattr(node, "body", [])
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            return body[0].lineno, body[0].value.value
        return None

    found = doc_of(tree)
    if found:
        out.append(found)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not node.name.startswith("_"):
            found = doc_of(node)
            if found:
                out.append(found)
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and not sub.name.startswith("_"):
                        found = doc_of(sub)
                        if found:
                            out.append(found)
    return out


def _next_code_line(lx: Lexed, idx: int) -> str:
    for k in range(idx + 1, len(lx.lines)):
        line = lx.code[k].strip()
        if not line:
            continue
        if line.startswith("#[") or line.startswith("#!["):
            continue
        return line
    return ""


def normalize(text: str) -> str:
    return " ".join(text.split())


def protected_comments(text: str, lang: str, constraint: re.Pattern) -> list[dict]:
    if lang not in CODE_LANGUAGES:
        return []
    lx = lex(text, lang)
    if lx is None:
        return []
    out = []
    for ln, body, kind in lx.comments:
        if not re.search(r"\w", body):
            continue  # empty or pure decoration carries no contract text
        classes = [name for name, pattern in PROTECTED_PATTERNS if pattern.search(body)]
        if constraint.search(body):
            classes.append("constraint")
        if lang == "rust" and kind == "inner_doc":
            classes.append("public_contract")
        elif lang == "rust" and kind == "doc" and _next_code_line(lx, ln - 1).startswith("pub"):
            classes.append("public_contract")
        elif lang == "js" and kind == "doc" and _next_code_line(lx, ln - 1).startswith("export"):
            classes.append("public_contract")
        for name in classes:
            out.append({"class": name, "line": ln, "text": normalize(body)})
    if lang == "python":
        for ln, doc in _py_public_docstrings(text):
            out.append({"class": "public_contract", "line": ln, "text": normalize(doc)})
    return out


def protected_tree(root: Path, rules: list[dict], scope=None, force=None) -> dict[str, list[dict]]:
    root = Path(root)
    force = force or {}
    constraint = constraint_regex(rules)
    result = {}
    for rel in walk_files(root):
        if not _in_scope(rel, scope):
            continue
        data = (root / rel).read_bytes()
        if rel not in force and scan_exclusion(rel, data):
            continue
        lang = force.get(rel) or detect_language(rel, data)
        if lang is None:
            continue
        entries = protected_comments(data.decode("utf-8", "replace"), lang, constraint)
        if entries:
            result[rel] = entries
    return result


def protected_counts(entries: list[dict]) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for entry in entries:
        key = (entry["class"], entry["text"])
        counts[key] = counts.get(key, 0) + 1
    return counts
