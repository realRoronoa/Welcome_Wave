"""Turn source files and documents into searchable chunks that remember exactly where they came from.

Code is split at function/class boundaries so a citation can point at real line numbers.
tree-sitter can replace the regex boundaries later without changing the Chunk contract.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.agents.schemas import Chunk

LANG_BY_EXT = {
    ".py": "python", ".go": "go", ".js": "javascript", ".jsx": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".java": "java", ".kt": "kotlin", ".rb": "ruby", ".rs": "rust",
    ".md": "markdown", ".yaml": "yaml", ".yml": "yaml", ".json": "json", ".sql": "sql", ".sh": "shell",
    ".toml": "toml", ".cfg": "ini", ".ini": "ini", ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp",
    ".cs": "csharp", ".php": "php", ".swift": "swift", ".scala": "scala", ".vue": "vue", ".svelte": "svelte",
}
# Files that matter by name, not extension: how to install, build and run the project. Any other .txt is data, left out.
LANG_BY_NAME = [
    (re.compile(r"^requirements[\w.-]*\.(?:txt|in)$", re.I), "requirements"),
    (re.compile(r"^(?:Dockerfile|Containerfile)(?:\.[\w-]+)?$"), "dockerfile"),
    (re.compile(r"^(?:GNUmakefile|Makefile|makefile|Justfile|Procfile)$"), "make"),
]
SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", "__pycache__", ".venv", "venv", "third_party"}
SKIP_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "go.sum", "poetry.lock"}
MAX_FILE_BYTES = 400_000
MAX_CHUNK_LINES = 120
WINDOW, OVERLAP = 80, 10

_DEF = {
    "python": re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+(?P<name>\w+)"),
    "go": re.compile(r"^(?:func\s+(?:\([^)]*\)\s*)?(?P<name>\w+)|type\s+(?P<name2>\w+)\s+(?:struct|interface))"),
    "javascript": re.compile(
        r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\s*\*?\s*(?P<name>\w+)|class\s+(?P<name2>\w+)|(?:const|let)\s+(?P<name3>\w+)\s*=\s*(?:async\s*)?(?:\(|function))"
    ),
    "java": re.compile(r"^\s*(?:public|private|protected)[\w\s<>\[\],]*?\s(?P<name>\w+)\s*\([^;]*$|^\s*(?:public\s+)?(?:class|interface|enum)\s+(?P<name2>\w+)"),
    "rust": re.compile(r"^\s*(?:pub\s+)?(?:fn|struct|enum|trait|impl)\s+(?P<name>\w+)"),
}
_DEF["typescript"] = _DEF["javascript"]
_DEF["kotlin"] = re.compile(r"^\s*(?:fun|class|object|interface)\s+(?P<name>\w+)")


def language_of(path: str) -> str | None:
    name = path.rsplit("/", 1)[-1]
    for pattern, lang in LANG_BY_NAME:
        if pattern.match(name):
            return lang
    for ext, lang in LANG_BY_EXT.items():
        if path.lower().endswith(ext):
            return lang
    return None


def should_index(path: str, size: int) -> bool:
    parts = path.split("/")
    if any(p in SKIP_DIRS for p in parts[:-1]) or parts[-1] in SKIP_FILES:
        return False
    return size <= MAX_FILE_BYTES and language_of(path) is not None


def module_of(path: str, depth: int = 2) -> str:
    """Coverage is reported per module: the first `depth` directories of a path."""
    dirs = path.split("/")[:-1]
    return "/".join(dirs[:depth]) if dirs else "(root)"


def split_identifiers(text: str) -> str:
    """validateResource / validate_resource -> 'validate resource', so keyword search finds both."""
    words = re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", text)
    return " ".join(w.lower() for w in words)


def _chunk_id(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:20]


@dataclass
class Symbol:
    name: str
    start_line: int
    end_line: int


def find_symbols(text: str, lang: str) -> list[Symbol]:
    pattern = _DEF.get(lang)
    lines = text.splitlines()
    if not pattern:
        return []
    starts = []
    for i, line in enumerate(lines, start=1):
        m = pattern.match(line)
        if m:
            name = next((v for k, v in m.groupdict().items() if v), None)
            if name:
                starts.append((i, name))
    symbols = []
    for idx, (line_no, name) in enumerate(starts):
        end = starts[idx + 1][0] - 1 if idx + 1 < len(starts) else len(lines)
        while end > line_no and not lines[end - 1].strip():
            end -= 1
        symbols.append(Symbol(name, line_no, end))
    return symbols


# Python: string literals (triple-quoted first) and comments, so neither a bracket nor an indentation in them is read as code
_PY_TOKENS = re.compile(r"(?s)(?P<triple>(\"\"\"|\'\'\')(?:\\.|(?!\2).)*(?:\2|$))|(?P<comment>#[^\n]*)|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'")
_C_TOKENS = re.compile(r"(?s)//[^\n]*|/\*.*?(?:\*/|$)|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*(?:`|$)")
_LEAD_COMMENT = re.compile(r"^\s*(?:/\*|\*|//|#)")


def _indent(text: str) -> int:
    expanded = text.expandtabs(4)
    return len(expanded) - len(expanded.lstrip())


def body_end(lines: list[str], start: int, end: int, lang: str) -> int:
    """The last line (1-based) of the symbol that starts at `start`, within its chunk range [start, end].

    find_symbols ends a symbol where the next one starts, so its range also holds what sits between them: the next
    symbol's decorators and leading comments, and module-level code such as `if __name__ == "__main__":`. Those lines are
    left out here. Python: the body ends before the first code line, outside brackets and strings, indented no deeper
    than the def line. Brace languages: at the line whose closing brace ends the block the symbol opened; without such a
    block, trailing decorators, comments and blank lines are dropped."""
    if end <= start:
        return start
    if lang == "python":
        text = "\n".join(lines[start - 1 : end])
        clean = list(text)
        in_string = [False] * (end - start + 1)  # a line that starts inside a triple-quoted string
        for m in _PY_TOKENS.finditer(text):
            for i in range(m.start(), m.end()):
                if clean[i] != "\n":
                    clean[i] = " "
            if m.group("triple"):
                first = text.count("\n", 0, m.start())
                for n in range(first + 1, first + 1 + m.group(0).count("\n")):
                    in_string[n] = True
        scrubbed = "".join(clean).split("\n")
        own = _indent(scrubbed[0])
        last, depth, cont = start, 0, False
        for n, (code, raw) in enumerate(zip(scrubbed, lines[start - 1 : end])):
            if n and code.strip() and depth == 0 and not cont and _indent(code) <= own:
                break  # module code, the next symbol's decorator, or a class body line after a method
            if code.strip() or in_string[n] or (raw.strip() and _indent(raw) > own):  # code, a string's lines, an inner comment
                last = start + n
            depth = max(0, depth + sum(code.count(ch) for ch in "([{") - sum(code.count(ch) for ch in ")]}"))
            cont = code.rstrip().endswith("\\")
        return last
    window = lines[start - 1 : end]
    if lang in ("go", "javascript", "typescript", "java", "kotlin", "rust"):
        scrubbed = _C_TOKENS.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), "\n".join(window)).split("\n")
        depth, opened = 0, False
        for n, code in enumerate(scrubbed):
            depth += code.count("{") - code.count("}")
            opened = opened or depth > 0
            if opened and depth <= 0:
                return start + n
    last = end
    while last > start and (not lines[last - 1].strip() or lines[last - 1].lstrip().startswith("@") or _LEAD_COMMENT.match(lines[last - 1])):
        last -= 1
    return last


def _is_code(line: str) -> bool:
    """A line of code: not blank, a decorator, a comment or punctuation alone (a closing brace)."""
    return bool(re.search(r"\w", line)) and not line.lstrip().startswith("@") and not _LEAD_COMMENT.match(line)


def chunk_code(project_id: str, path: str, text: str, commit: str = "") -> list[Chunk]:
    lang = language_of(path) or "text"
    lines = text.splitlines()
    if not lines:
        return []
    spans: list[tuple[int, int, str | None]] = []
    ends: dict[int, int] = {}  # a symbol's first line -> its own last line (body_end), stamped on each of its chunks
    symbols = find_symbols(text, lang)
    if symbols:
        if symbols[0].start_line > 1:
            spans.append((1, symbols[0].start_line - 1, None))
        for sym in symbols:
            start = sym.start_line
            last = body_end(lines, sym.start_line, sym.end_line, lang)
            # Module code after the body (`if __name__ == "__main__":` after the last function, a constant between two) is not
            # part of the symbol: it gets a chunk of its own, so a citation of the function never quotes it. The next symbol's
            # decorators, comments and lone closing braces stay where they were.
            stop, tail = sym.end_line, None
            if last < sym.end_line and any(_is_code(line) for line in lines[last : sym.end_line]):
                stop, tail = last, (last + 1, sym.end_line)
            while start <= stop:  # split very long functions
                end = min(stop, start + MAX_CHUNK_LINES - 1)
                spans.append((start, end, sym.name))
                ends[start] = last
                start = end + 1
            if tail:
                spans.append((tail[0], tail[1], None))
    else:
        start = 1
        while start <= len(lines):
            end = min(len(lines), start + WINDOW - 1)
            spans.append((start, end, None))
            if end == len(lines):
                break
            start = end - OVERLAP + 1

    chunks = []
    for start, end, name in spans:
        body = "\n".join(lines[start - 1 : end])
        if not body.strip():
            continue
        header = f"File: {path} (lines {start}-{end})" + (f"\nSymbol: {name}" if name else "")
        chunks.append(
            Chunk(
                # Not keyed by the commit: a span that a re-index finds unchanged keeps its id, so the answers and KT links
                # that cite it still match, and a later push that changes it marks them.
                id=_chunk_id(project_id, path, start, end),
                project_id=project_id,
                source_type="code",
                symbol=name,
                ref={"path": path, "start_line": start, "end_line": end, "commit": commit, "language": lang,
                     **({"body_end": ends[start]} if start in ends else {})},
                content=f"{header}\n{body}",
            )
        )
    return chunks


def chunk_document(project_id: str, document_id: str, title: str, pages: list[str], target_chars: int = 1200) -> list[Chunk]:
    chunks = []
    for page_no, page in enumerate(pages, start=1):
        buf = ""
        for para in re.split(r"\n\s*\n", page):
            para = para.strip()
            if not para:
                continue
            if buf and len(buf) + len(para) > target_chars:
                chunks.append(_doc_chunk(project_id, document_id, title, page_no, buf, len(chunks)))
                buf = ""
            buf = f"{buf}\n\n{para}" if buf else para
        if buf:
            chunks.append(_doc_chunk(project_id, document_id, title, page_no, buf, len(chunks)))
    return chunks


def _doc_chunk(project_id, document_id, title, page, text, n) -> Chunk:
    return Chunk(
        id=_chunk_id(project_id, document_id, page, n),
        project_id=project_id,
        source_type="doc",
        ref={"document_id": document_id, "title": title, "page": page},
        content=f"Document: {title} (page {page})\n{text}",
    )


def chunk_transcript(project_id: str, meeting_id: str, title: str, utterances, window_s: float = 60.0) -> list[Chunk]:
    chunks, buf, start = [], [], None
    for u in utterances:
        if start is None:
            start = u.start_s
        buf.append(f"[{_ts(u.start_s)}] {u.speaker}: {u.text}")
        if u.end_s - start >= window_s:
            chunks.append(_meeting_chunk(project_id, meeting_id, title, start, u.end_s, buf))
            buf, start = [], None
    if buf:
        chunks.append(_meeting_chunk(project_id, meeting_id, title, start, utterances[-1].end_s, buf))
    return chunks


def _meeting_chunk(project_id, meeting_id, title, start, end, lines) -> Chunk:
    return Chunk(
        id=_chunk_id(project_id, meeting_id, start),
        project_id=project_id,
        source_type="meeting",
        ref={"meeting_id": meeting_id, "title": title, "start_s": start, "end_s": end},
        content=f"Meeting: {title} ({_ts(start)}-{_ts(end)})\n" + "\n".join(lines),
    )


def _ts(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"
