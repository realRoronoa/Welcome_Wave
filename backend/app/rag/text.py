"""One tokenizer for the crew, so the Planner, the keyword channel and the Judge read text the same way.

Words are identifier-aware (`check_labels` and `checkLabels` both read as "check labels", via chunking.split_identifiers),
lower-case, without stop words, and stemmed lightly so a word's common forms meet ("labels", "labelled" -> "label";
"validate", "validation", "validator" -> "valid"). `identifiers` keeps the names a text gives exactly as written (paths,
snake_case, camelCase, dotted and back-ticked names): those are looked up as they are, not as words.

Deliberately not app.lifecycle.routing's IDENT: the crew must not import the lifecycle (it would be a cycle), and it reads a
few more shapes of name (back-ticks, `name()`, PascalCase)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from app.services.chunking import split_identifiers

# Grammar and question words: they say nothing about what is asked, in a question or in a source.
STOP = frozenset("""
a an the and or but nor not no yes of to in on at for from by with without into onto over under about above below between
is are was were be been being am do does did doing done have has had having can could should would will shall may might must
i me my mine we our ours us you your yours he him his she her hers it its they them their theirs this that these those there here
what which who whom whose when where why how whats whos wheres hows
if then than else so as too very just also only any some all each every both either neither such own same other others
up down out off again further once more most less least much many few
let lets please thanks thank hi hello hey ok okay
""".split())

# Words that a question about a code base uses for its own shape, not its subject ("which FILE defines the FUNCTION that ..."):
# kept out of the subject, so "holds 3 of 3 subject words" counts the words that matter.
GENERIC = frozenset("""
function functions method methods file files code codes module modules class classes line lines project projects repo repos
repository repositories codebase thing things way ways part parts place places stuff
work works working worked happen happens happened make makes made get gets got use uses used using need needs want wants
know knows mean means find look see tell show explain describe understand new engineer engineers team
define defines defined definition implement implements implemented implementation located location live lives
someone somebody something anyone anything everyone everything one ones kind kinds sort type good best right first
""".split())

# File extensions that make "word.ext" a file name rather than a sentence's full stop or a dotted name.
EXTENSIONS = ("py", "pyi", "js", "jsx", "mjs", "cjs", "ts", "tsx", "go", "java", "kt", "kts", "rb", "rs", "md", "rst", "txt", "yaml",
              "yml", "json", "toml", "sql", "sh", "bash", "cfg", "ini", "env", "c", "h", "cpp", "hpp", "cc", "cs", "php", "swift",
              "scala", "vue", "svelte", "html", "css", "scss", "proto", "tf", "gradle", "lock", "xml", "csv", "ipynb")
NOT_IDENT = frozenset({"e.g", "i.e", "etc", "vs", "a.m", "p.m", "u.s"})

# Light suffix stripping, first applicable rule wins (the stem must keep at least 3 letters). Then a final "e" goes and a
# doubled final consonant collapses, so "raise/raises/raised/raising" and "install/installing" meet.
_SUFFIXES = (
    ("ations", ""), ("ation", ""), ("ators", ""), ("ator", ""), ("ating", ""), ("ated", ""), ("ates", ""), ("ate", ""),
    ("ingly", ""), ("ings", ""), ("ing", ""),
    ("ied", "y"), ("ies", "y"),
    ("edly", ""), ("ed", ""),
    ("sses", "ss"), ("xes", "x"), ("ches", "ch"), ("shes", "sh"), ("zes", "z"),
    ("ss", "ss"), ("us", "us"), ("is", "is"),
    ("s", ""),
)
_KEEP = frozenset({"embed", "always", "news", "series", "species", "canvas", "alias", "atlas", "bias", "redis", "kubernetes",
                   "postgres", "aws", "gcs", "https", "jenkins", "always", "perhaps", "less", "unless"})
_VOWELS = frozenset("aeiou")


@lru_cache(maxsize=65536)
def stem(word: str) -> str:
    """'labels' -> 'label', 'raising' -> 'rais', 'validation' -> 'valid', 'running' -> 'run'. Enough to match a word's forms;
    not a linguist's stemmer. Words with digits, and very short ones, stay as they are."""
    w = word.lower()
    if len(w) <= 3 or w in _KEEP or not w.isalpha():
        return w
    for suffix, repl in _SUFFIXES:
        if not w.endswith(suffix):
            continue
        if suffix == "ed" and w.endswith("eed"):
            break
        base = w[: len(w) - len(suffix)] + repl
        if len(base) >= 3:
            w = base
            break
        if suffix == "ed" and len(w) - 1 >= 3:  # "used" -> "use"
            w = w[:-1]
            break
    if w.endswith("e") and len(w) - 1 >= 3:
        w = w[:-1]
    if len(w) >= 4 and w[-1] == w[-2] and w[-1] not in _VOWELS and w[-1].isalpha():
        w = w[:-1]
    return w


def words(text: str) -> list[str]:
    """The text's identifier-aware words, lower-case and in order (stop words kept)."""
    return split_identifiers(text).split()


def tokens(text: str) -> list[str]:
    """Stemmed content words in order: what BM25 counts and what phrase matching walks. Stop words and single letters go."""
    return [stem(w) for w in words(text) if len(w) > 1 and w not in STOP]


_COMPOUND = re.compile(r"\b_*[A-Za-z][A-Za-z0-9]*(?:_+[A-Za-z0-9]+)+_*\b|\b[a-z]+[A-Z][A-Za-z0-9]*\b|\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+\b")
NAME_MARK = "~"  # marks a whole identifier among the words: '~min_similarity' never collides with the word 'min'


def index_terms(text: str) -> list[str]:
    """tokens(text), plus every compound identifier it holds as one term of its own ('~min_similarity', '~checklabels'):
    the keyword channel then matches a name the question gives exactly, a rare term, instead of only its common words."""
    return tokens(text) + [NAME_MARK + m.group(0).lower() for m in _COMPOUND.finditer(text or "")]


@dataclass(frozen=True)
class Terms:
    tokens: tuple[str, ...]
    counts: dict[str, int]
    pairs: frozenset[tuple[str, str]]  # adjacent token pairs: "default label" in a source matches the question's phrase


@lru_cache(maxsize=4096)
def terms(text: str) -> Terms:
    """tokens() with counts and adjacent pairs, cached by text: the Judge scores the same sources for question after question."""
    toks = tuple(tokens(text))
    counts: dict[str, int] = {}
    for t in toks:
        counts[t] = counts.get(t, 0) + 1
    return Terms(toks, counts, frozenset(zip(toks, toks[1:])))


def subject_words(text: str) -> list[str]:
    """The words that say what a text is about, as written (lower-case), in order, one per stem: no stop words, none of the
    words a question uses for the code's shape ('file', 'function'), nothing shorter than 3 letters."""
    out, seen = [], set()
    for w in words(_URL.sub(" ", text or "")):
        if len(w) <= 2 or w in STOP or w in GENERIC or w.isdigit():
            continue
        s = stem(w)
        if s not in seen:
            seen.add(s)
            out.append(w)
    return out


def subject(text: str) -> set[str]:
    """The stems of subject_words(text)."""
    return {stem(w) for w in subject_words(text)}


# ---------------------------------------------------------------- names, as written
_URL = re.compile(r"\b(?:https?|ftp)://\S+|\bwww\.\S+", re.I)
_BACKTICK = re.compile(r"`([^`\s]{1,160})`")
_EXT = "|".join(sorted(EXTENSIONS, key=len, reverse=True))
_PATH = re.compile(r"(?<![\w.~-])(?:\.{1,2}/|/)?(?:[\w.-]+/)+[\w.-]*|(?<![\w/.-])[\w-]+(?:\.[\w-]+)*\.(?:" + _EXT + r")\b")
_DOTTED = re.compile(r"(?<![\w.])[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")
_SNAKE = re.compile(r"\b_*[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]*[A-Za-z0-9]_*\b|\b__\w+__\b")
_CAMEL = re.compile(r"\b[a-z]+[A-Z][A-Za-z0-9]*\b")
_PASCAL = re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+\b")
_CALL = re.compile(r"\b([A-Za-z_]\w*)\(\)")
_FILE_EXT = re.compile(r"\.(?:" + _EXT + r")$", re.I)


def _plausible_path(p: str) -> bool:
    """'engine/policy.py', 'app/rag/', './run.sh', '/api/ask' are paths; 'and/or', 'input/output', 'yes/no' are English."""
    if _FILE_EXT.search(p) or p.endswith("/") or p.startswith(("/", "./", "../")):
        return True
    parts = [x for x in p.split("/") if x]
    return len(parts) >= 3 or any("_" in x or "." in x or "-" in x for x in parts)


def identifiers(text: str) -> list[str]:
    """The names a text gives, as written and in order: paths ('engine/policy.py', 'policy.py'), snake_case and UPPER_SNAKE,
    camelCase and PascalCase with two humps, dotted names ('app.config', 'Rule.kind'), `back-ticked` single tokens and
    `name()` calls. A name inside a longer one ('policy.py' in 'engine/policy.py') is not repeated. URLs are left out."""
    text = _URL.sub(" ", text or "")
    spans: list[tuple[int, int, str]] = []
    for m in _BACKTICK.finditer(text):
        spans.append((m.start(1), m.end(1), m.group(1).strip("().,;:")))
    for m in _PATH.finditer(text):
        p = m.group(0).rstrip(".,;:")
        if p and "/" in p and not _plausible_path(p):
            continue
        spans.append((m.start(), m.start() + len(p), p))
    for rx in (_DOTTED, _SNAKE, _CAMEL, _PASCAL):
        for m in rx.finditer(text):
            spans.append((m.start(), m.end(), m.group(0)))
    for m in _CALL.finditer(text):
        spans.append((m.start(1), m.end(1), m.group(1)))
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    out: list[str] = []
    taken: list[tuple[int, int]] = []
    for start, end, name in spans:
        if not name or name.lower() in NOT_IDENT or name.replace(".", "").isdigit() or len(name) > 200:
            continue
        if any(a <= start and end <= b for a, b in taken):
            continue
        taken.append((start, end))
        if name not in out:
            out.append(name)
    return out


def is_path(name: str) -> bool:
    """A name that points at a file or folder rather than a symbol."""
    return "/" in name or bool(_FILE_EXT.search(name))


def symbol_of(name: str) -> str:
    """The symbol a name refers to: 'Rule.kind' -> 'kind', 'check_labels()' -> 'check_labels'."""
    return name.strip("()").rsplit(".", 1)[-1]


def qualifier_of(name: str) -> str | None:
    """What a dotted name's symbol belongs to: 'ReportJob.run' -> 'ReportJob', 'a.b.c' -> 'b'; None for a bare name or a path."""
    if is_path(name):
        return None
    parts = name.strip("()").split(".")
    return parts[-2] if len(parts) >= 2 and parts[-2] and parts[-1] else None


def is_test_path(path: str) -> bool:
    """A test file: tests/ or test/ folders, test_*.py, *_test.go, *.test.ts, *.spec.js, conftest.py."""
    p = (path or "").lower()
    base = p.rsplit("/", 1)[-1]
    return (p.startswith(("tests/", "test/", "__tests__/")) or any(d in p for d in ("/tests/", "/test/", "/__tests__/", "/spec/"))
            or base.startswith("test_") or base == "conftest.py" or re.search(r"(?:_test|\.test|\.spec|_spec)\.\w+$", base) is not None)
