"""The Judge's lexical cross-scorer: every fused candidate is scored against the question itself, not just by where the
searches ranked it.

    stats = pool_stats(analysis, candidates)       # once per pool: fused ranks, BM25 over the pool, the question's names
    f = features(analysis, hit, stats)             # numbers in 0-1 (and the facts reasons() quotes)
    score(f)                                       # sum of WEIGHTS[name] * f[name]
    reasons(f)                                     # short lines in our words: "defines check_labels", "holds 3 of 3 subject words"

The features, and why each one:
- fused: where weighted RRF over every (query, kind) list put it; agreement between searches is evidence.
- anchor: its cosine to the question as asked, relative to the pool's best (or ANCHOR_FLOOR when the best is lower): the
  meaning of the whole question.
- bm25: BM25 of the question's subject words over the pool (relative to the best), so a rare word counts more than a common one.
- cover / label / symname: the share of subject words the source holds, the share in its label (path, title), and the
  share the name of something it defines holds ("font colour" -> font_colour(); "page width" -> PAGE_WIDTH =).
  A subject word held in another form counts ALT_CREDIT: an alternate the Planner found (analyze.alternates_of: 'encoder'
  -> encode, 'parameter' -> param, 'time stamp' -> timestamp), or the pool's own spelling of a word no candidate holds as
  the question writes it ('recieve' -> receive: one edit away, same first letter; see typo_fixes).
- symbol / path / named: the source IS what the question names (it defines the function, class or constant; it is the file),
  or mentions those names. A folder the question names ('engine/') counts FOLDER_PATH: the source is in the area asked
  about, not the file asked for. head: it is the top of a file the question names (its docstring says what it is for).
- phrase: the question's adjacent subject words are adjacent in the source too ("default label"), or written as one word.
- plan: the most of one model query's words the source holds. The model planner's rewrites find what the question's own
  words miss; without this, the lexical judge (which reads the question) would bury those finds whenever the model judge
  is off or late.
- prior: what the question's intent makes a kind of source worth (a how-to favours documents, a "where" favours code). A
  repository's own documentation (README.md, docs/*.md: indexed as code) counts as a document here.
- asked: the source is the kind the question names ("in the KT call", "according to the README").
- test: a test file when the question is not about tests (small: tests are often the clearest example, but rarely the answer).

The weights are module constants: names a question gives are the strongest evidence, then subject coverage and the
question's own similarity, then the rest. They were set by reading, then checked against the dev split of
backend/eval/golden_*.jsonl with backend/eval/rag_eval.py (never the holdout)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from app.agents.core.schemas import Chunk, Hit
from app.rag import text as T
from app.rag.analyze import Analysis
from app.rag.bm25 import BM25

WEIGHTS: dict[str, float] = {
    "fused": 0.5,  # the hybrid lists' agreement: half the anchor's weight, because the lexical features below re-read the
                   # same evidence more precisely (measured on the dev split: 1.0 buried exact matches under fused noise)
    "anchor": 1.2,
    "bm25": 1.2,
    "cover": 1.4,
    "label": 0.6,
    "symname": 0.8,
    "symbol": 2.0,
    "path": 1.5,
    "head": 1.0,
    "named": 1.0,
    "phrase": 0.6,
    "plan": 1.2,
    "prior": 1.0,
    "asked": 0.8,
    "test": -0.35,
}
# With an embedder that reads meaning (a model's embeddings), the question's own similarity is real evidence of meaning, not
# a second word match: it carries more, read from the pool's lowest measured similarity to its best (pool_stats). Set on the
# demo's dev split with real embeddings (gemini-embedding-2, replayed from a cache): the offline weights lost to plain
# Index.search (nDCG 0.89 vs 0.92); with this reading and 3.6 to 4.8 the crew leads (0.92-0.93, MRR 0.96 vs 0.91); the
# holdout then tied it (nDCG 0.90 vs 0.91, MRR equal). The offline weights above stay as they are: the hashing embedder's
# similarity is a word match.
SEMANTIC_WEIGHTS: dict[str, float] = {**WEIGHTS, "anchor": 4.0}
FUSED_HALF = 3  # the fused feature halves every this many places: rank 1 -> 1.0, rank 4 -> 0.5, rank 10 -> 0.25
UNMEASURED = 0.5  # the anchor feature of a source only an exact-name lookup found: its similarity is unknown, not low
ANCHOR_FLOOR = 0.4  # similarity is read relative to the pool's best, but never to a best below this: a pool of noise stays noise
ALT_CREDIT = 0.75  # a subject word held in another form, not as the question writes it
FOLDER_PATH = 0.5  # the path feature of a source inside a folder the question names (a file it names counts 1.0)
TYPO_MIN = 5  # a word this long or longer, held by no candidate, is matched to the pool's spelling one edit away
MAX_TYPO_FIXES = 3  # spellings taken per misspelt word
DOC_EXTENSIONS = (".md", ".markdown", ".rst", ".txt", ".adoc")  # a repository's documentation, indexed as code

KIND_WORDS = {"code": "code", "doc": "a document", "meeting": "a KT call", "verified_answer": "a verified answer"}
INTENT_WORDS = {"locate": "a where-is", "explain": "an explain", "howto": "a how-to", "why": "a why", "people": "a who-knows",
                "overview": "an overview"}


def label_text(c: Chunk, symbol: bool = True) -> str:
    """What a source is called: its path and symbol, its document or call title, or the question it verified."""
    r = c.ref or {}
    if c.source_type == "code":
        return f"{r.get('path', '')} {c.symbol or ''}" if symbol else str(r.get("path", ""))
    if c.source_type == "verified_answer":
        return str(r.get("question", ""))
    return str(r.get("title", ""))


def file_key(c: Chunk) -> str | None:
    """The file a source comes from (a code path or a document), for 'at most 3 hits per file'; None for the rest."""
    if c.source_type == "code":
        return f"code:{(c.ref or {}).get('path', '')}"
    if c.source_type == "doc":
        return f"doc:{(c.ref or {}).get('document_id', '')}"
    return None


def kind_of(c: Chunk) -> str:
    """The kind of source a chunk is to a reader: its source type, except that a repository's documentation (README.md,
    docs/*.md, indexed as code) is a document."""
    if c.source_type == "code" and str((c.ref or {}).get("path", "")).lower().endswith(DOC_EXTENSIONS):
        return "doc"
    return c.source_type


@dataclass
class PoolStats:
    size: int
    rank: dict[str, int]  # chunk id -> place in the fused list (1 = best)
    bm25: dict[str, float]  # chunk id -> BM25 of the subject words over the pool, relative to the best (0-1)
    max_sim: float  # the pool's best similarity to the question as asked
    min_sim: float = 0.0  # where the anchor feature starts: 0, or for an embedder that reads meaning the pool's lowest measured
    symbols: list[str] = field(default_factory=list)  # the names the question gives that are symbols ('Rule.kind' -> 'kind')
    symbol_names: list[str] = field(default_factory=list)  # ...the same names as written ('Rule.kind'), in the same order
    resolved: dict[str, frozenset[str]] = field(default_factory=dict)  # qualified name -> ids of the chunks that define it
    paths: list[str] = field(default_factory=list)  # ...that are paths, without './' or a leading '/'
    modules: list[str] = field(default_factory=list)  # dotted names as the path they may be ('engine.policy' -> 'engine/policy')
    patterns: list[tuple[str, re.Pattern]] = field(default_factory=list)  # each name as written -> where text mentions it
    defines: list[tuple[str, re.Pattern]] = field(default_factory=list)  # each symbol name -> where code defines or assigns it
    declares: list[tuple[str, re.Pattern]] = field(default_factory=list)  # ...where code defines it by keyword (def, class ...)
    unmeasured: frozenset[str] = frozenset()  # ids only an exact-name lookup found (no similarity was measured)
    path_words: frozenset[str] = frozenset()  # subject stems that come from a path the question gives (every chunk's label has them)
    plan_terms: list[frozenset[str]] = field(default_factory=list)  # each model query's words (stems)
    alternates: dict[str, frozenset[str]] = field(default_factory=dict)  # subject stem -> other forms (the Planner's and typo fixes)
    texts: dict[str, str] = field(default_factory=dict)  # chunk id -> the text its words are read from, when not its own


def _clean_path(p: str) -> str:
    """'./engine/x.py' and '/engine/x.py' -> 'engine/x.py'; '.github/CODEOWNERS' keeps its dot."""
    p = p.strip()
    while p.startswith(("./", "../")):
        p = p[p.index("/") + 1:]
    return p.lstrip("/")


def path_matches(path: str, given: str) -> bool:
    """A file path is the one a question names: the same path, or one that ends with it ('policy.py' names
    'engine/policy.py'); a folder ('app/rag/') names the files in it."""
    g = _clean_path(given)
    if not g or not path:
        return False
    if g.endswith("/"):
        return path.startswith(g) or f"/{g}" in f"/{path}"
    return path == g or path.endswith("/" + g)


def _edits(a: str, b: str, limit: int) -> int:
    """Optimal-string-alignment distance (insert, delete, substitute, swap two neighbours), or limit + 1 once it is over."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def typo_fixes(analysis: Analysis, vocabulary: set[str]) -> dict[str, frozenset[str]]:
    """Subject stem -> the pool's spellings of it, for a word of at least TYPO_MIN letters that no candidate holds in any of
    its forms: the words one edit away (two for a word of 8 letters or more) that start with the same letter. A question
    typed in a hurry ('recieve', 'lenght') still meets the sources that spell it right."""
    out: dict[str, frozenset[str]] = {}
    for s in analysis.subject:
        if len(s) < TYPO_MIN or s in vocabulary or not s.isalpha() or analysis.alternates.get(s, frozenset()) & vocabulary:
            continue
        limit = 2 if len(s) >= 8 else 1
        near = sorted((_edits(s, v, limit), v) for v in vocabulary
                      if v[:1] == s[:1] and abs(len(v) - len(s)) <= limit and v.isalpha() and v not in analysis.subject)
        fixes = [v for d, v in near if d <= limit][:MAX_TYPO_FIXES]
        if fixes:
            out[s] = frozenset(fixes)
    return out


def read_alternates(analysis: Analysis, hits: list[Hit], texts: dict[str, str] | None = None) -> dict[str, frozenset[str]]:
    """The subject words' other forms (analysis.alternates) plus the typo fixes this pool's vocabulary gives (typo_fixes)."""
    texts = texts or {}
    vocabulary = {t for h in hits for t in T.terms(texts.get(h.chunk.id, h.chunk.content) + "\n" + label_text(h.chunk)).counts}
    alternates = dict(analysis.alternates)
    for s, fixes in typo_fixes(analysis, vocabulary).items():
        alternates[s] = alternates.get(s, frozenset()) | fixes
    return alternates


def pool_stats(analysis: Analysis, hits: list[Hit], unmeasured=frozenset(), resolved: dict[str, frozenset[str]] | None = None,
               semantic: bool = False, texts: dict[str, str] | None = None,
               alternates: dict[str, frozenset[str]] | None = None) -> PoolStats:
    """What features() compares each candidate with: computed once for the whole fused list (best first). `unmeasured`: the
    ids only an exact-name lookup found. `resolved`: each qualified name the exact lookups found inside its owner
    ('ReportJob.run' -> the run of ReportJob), with the ids of the chunks that define it: only those define that name.

    A dotted name is mentioned where its full form is ('ReportJob.run'); when no candidate holds the full form, its last
    part stands in, and is reported as that part ('mentions run'), never as the full name.

    `semantic`: the similarities come from an embedder that reads meaning. Its cosines sit in a narrow band (0.6-0.8 for
    every source of a project), so the anchor feature reads them from the pool's lowest measured one to its best: the
    question's own meaning then separates the candidates instead of giving all of them about the same credit.

    `texts`: chunk id -> the text its words are read from instead of its own (a bare `class X:` line read with its first
    member: the crew's context). Only the word features read it; what a chunk defines, names or mentions is its own text.

    `alternates`: the subject words' other forms, typo fixes included, as an earlier pool_stats found them (PoolStats
    .alternates): a pool that grew (the crew's expansion) then reads the question the same way, instead of dropping a typo
    fix because some added chunk happens to hold the misspelt word."""
    texts = dict(texts or {})
    resolved = dict(resolved or {})
    measured = [h.similarity for h in hits if h.chunk.id not in unmeasured and h.similarity > 0]
    names = analysis.identifiers
    bodies = [T.terms(texts.get(h.chunk.id, h.chunk.content) + "\n" + label_text(h.chunk)).counts for h in hits]
    alternates = dict(alternates) if alternates is not None else read_alternates(analysis, hits, texts)
    query = set(analysis.subject) | {t for n in names for t in T.tokens(n)}
    bm = BM25([_fold(b, alternates) for b in bodies])
    raw = bm.scores(query)
    best = max(raw.values(), default=0.0)
    patterns, defines, declares = [], [], []
    for n in names:
        if T.is_path(n):
            patterns.append((n, re.compile(re.escape(_clean_path(n).rstrip("/")))))
        else:
            sym = T.symbol_of(n)
            if len(sym) >= 2:
                whole = n.strip("()")
                full = re.compile(rf"(?<![\w.]){re.escape(whole)}(?![\w])")
                if whole != sym and any(full.search(h.chunk.content) for h in hits):
                    patterns.append((n, full))
                else:
                    patterns.append((sym if whole != sym else n, re.compile(rf"(?<![\w]){re.escape(sym)}(?![\w])")))
                if n not in resolved:
                    defines.append((sym, _definition(sym)))
                    declares.append((sym, _definition(sym, assignments=False)))
    return PoolStats(size=len(hits), rank={h.chunk.id: i + 1 for i, h in enumerate(hits)},
                     bm25={h.chunk.id: (raw.get(i, 0.0) / best if best > 0 else 0.0) for i, h in enumerate(hits)},
                     max_sim=max((h.similarity for h in hits), default=0.0),
                     min_sim=min(measured) if semantic and len(measured) >= 2 else 0.0,
                     symbols=[T.symbol_of(n) for n in names if not T.is_path(n)],
                     symbol_names=[n for n in names if not T.is_path(n)], resolved=resolved,
                     modules=[n.strip("()").replace(".", "/") for n in names if not T.is_path(n) and "." in n.strip("()")],
                     paths=[_clean_path(n) for n in names if T.is_path(n)], patterns=patterns, defines=defines, declares=declares,
                     unmeasured=frozenset(unmeasured),
                     path_words=frozenset(t for n in names if T.is_path(n) for t in T.tokens(n)) & analysis.subject,
                     plan_terms=[t for t in (frozenset(T.tokens(q.text)) for q in analysis.queries if q.origin == "model") if t],
                     alternates=alternates, texts=texts)


def definition_pattern(name: str) -> re.Pattern:
    """Where code defines a name (see _definition), or declares it as a typed field alone on its line (`kind: str`, a
    dataclass's or an interface's member), for the crew's exact lookups of a class's members."""
    n = re.escape(name)
    return re.compile(_definition(name).pattern + rf"|^[ \t]*{n}[ \t]*:[^=\n]*$", re.M)


def _definition(name: str, assignments: bool = True) -> re.Pattern:
    """Where code defines a name: `def name`, `class name`, `function name`, `const name` ... or, with `assignments`,
    `name = ...` / `name: T = ...` at the start of a line (a module constant, a setting, a class attribute)."""
    n = re.escape(name)
    assign = rf"|{n}[ \t]*(?::[^=\n]*)?=(?!=)" if assignments else ""
    return re.compile(rf"(?m)^[ \t]*(?:(?:export\s+)?(?:async\s+)?(?:def|class|function|func|fn|struct|enum|interface|type|const|let|var)"
                      rf"\s+{n}\b{assign})")


_FUNCTION_HEAD = re.compile(r"^(?:File|Symbol): .*\n(?:(?:File|Symbol): .*\n)*(?:[ \t]*@.*\n|[ \t]*\n)*[ \t]*"
                            r"(?:export\s+)?(?:async\s+)?(?:def|function|func|fn)\b")


def _in_function(c: Chunk) -> bool:
    """A chunk that is a function or method: an assignment in it is a parameter or a local, never the definition of a name."""
    return bool(c.symbol) and _FUNCTION_HEAD.match(c.content) is not None


# A name a chunk defines: def/class/function ..., `NAME = ...`, `name: T = ...` or a typed field `name: T` alone on its line.
_DEFINED = re.compile(r"(?m)^[ \t]*(?:(?:export[ \t]+)?(?:async[ \t]+)?(?:def|class|function|func|fn|struct|enum|interface|const|let|var)"
                      r"[ \t]+([A-Za-z_]\w*)|([A-Za-z_]\w*)[ \t]*(?::[^=\n]*)?=(?!=)|([A-Za-z_]\w*)[ \t]*:[ \t]*[A-Za-z_][\w\[\]., |]*$)")


@lru_cache(maxsize=8192)
def defined_names(content: str) -> tuple[frozenset[str], ...]:
    """The stems of each name a code chunk defines, once per distinct text (the Judge sees the same chunks again and again)."""
    out = []
    for m in _DEFINED.finditer(content):
        name = next(g for g in m.groups() if g)
        toks = frozenset(T.tokens(name))
        if toks and toks not in out:
            out.append(toks)
    return tuple(out)


def _fold(counts: dict[str, int], alternates: dict[str, frozenset[str]]) -> dict:
    """A source's word counts with each subject word's other forms counted as that word (at ALT_CREDIT), so BM25 weighs
    'param' for a question that says 'parameter'."""
    extra = {s: ALT_CREDIT * n for s, alts in alternates.items() if (n := sum(counts.get(a, 0) for a in alts))}
    if not extra:
        return counts
    out: dict[str, float] = dict(counts)
    for s, n in extra.items():
        out[s] = out.get(s, 0) + n
    return out


def _held(stems, stats: PoolStats, keys) -> tuple[int, int, float]:
    """Of `stems`: how many the text holds as written, how many more only in another form, and the credit (1 each as
    written, ALT_CREDIT in another form)."""
    literal = sum(1 for s in stems if s in keys)
    other = sum(1 for s in stems if s not in keys and stats.alternates.get(s, frozenset()) & keys)
    return literal, other, literal + ALT_CREDIT * other


def _share(stems, stats: PoolStats, keys) -> float:
    return _held(stems, stats, keys)[2] / len(stems) if stems else 0.0


def features(analysis: Analysis, hit: Hit, stats: PoolStats) -> dict:
    """The candidate's features (see the module docstring): numbers for score(), and the facts reasons() quotes."""
    c = hit.chunk
    label = label_text(c)
    body = T.terms(stats.texts.get(c.id, c.content) + "\n" + label)
    keys = body.counts.keys()
    subj = analysis.subject
    alts = stats.alternates
    literal, other, credit = _held(subj, stats, keys)
    rank = stats.rank.get(c.id, stats.size or 1)
    kind = kind_of(c)
    path = (c.ref or {}).get("path", "") if c.source_type == "code" else ""
    title = str((c.ref or {}).get("title", "")) if c.source_type == "doc" else ""
    symbol_hit = ""
    for given, s in zip(stats.symbol_names, stats.symbols):
        ids = stats.resolved.get(given)
        if ids is not None:  # a qualified name found inside its owner: only those chunks define it
            if c.id in ids:
                symbol_hit = given
                break
        elif c.symbol and c.symbol == s:
            symbol_hit = s
            break
    if not symbol_hit and c.source_type == "code":
        symbol_hit = next((s for s, rx in (stats.declares if _in_function(c) else stats.defines) if rx.search(c.content)), "")
    in_label = set(T.tokens(label_text(c, symbol=False)))
    file_hit = next((p for p in stats.paths if not p.endswith("/") and ((path and path_matches(path, p)) or (title and _names_document(title, p)))), "")
    if not file_hit and path and stats.modules:  # 'engine.policy' names engine/policy.py
        stem = path.rsplit(".", 1)[0] if "." in path.rsplit("/", 1)[-1] else path
        file_hit = path if any(stem == m or stem.endswith("/" + m) for m in stats.modules) else ""
    folder_hit = "" if file_hit else next((p for p in stats.paths if p.endswith("/") and path and path_matches(path, p)), "")
    named = [n for n, rx in stats.patterns if rx.search(c.content) or c.id in stats.resolved.get(n, ())]  # X.y names itself
    pairs = analysis.pairs
    held_pairs = sum(1 for a, b in pairs if (a, b) in body.pairs or alts.get(a, frozenset()) & alts.get(b, frozenset()) & keys)
    own = subj - stats.path_words
    names = ([frozenset(T.tokens(c.symbol))] if c.symbol else []) + (list(defined_names(c.content)) if kind == "code" else [])
    return {
        "fused": 1.0 / (1.0 + (rank - 1) / FUSED_HALF),
        "anchor": UNMEASURED if c.id in stats.unmeasured else _anchor(hit.similarity, stats),
        "bm25": stats.bm25.get(c.id, 0.0),
        "cover": credit / len(subj) if subj else 0.0,
        "label": _share(subj, stats, in_label),
        "symname": max((_share(own, stats, n) for n in names), default=0.0),
        "symbol": 1.0 if symbol_hit else 0.0,
        "path": 1.0 if file_hit else FOLDER_PATH if folder_hit else 0.0,
        "head": 1.0 if file_hit and path and int((c.ref or {}).get("start_line") or 0) == 1 else 0.0,
        "named": len(named) / len(stats.patterns) if stats.patterns else 0.0,
        "phrase": held_pairs / len(pairs) if pairs else 0.0,
        "plan": max((len(t & keys) / len(t) for t in stats.plan_terms), default=0.0),
        "prior": analysis.priors.get(kind, 0.8),
        "asked": 1.0 if kind in analysis.asks_for else 0.0,
        "test": 1.0 if path and kind == "code" and T.is_test_path(path) and not analysis.about_tests else 0.0,
        # facts for reasons(), not scored
        "_rank": rank, "_size": stats.size, "_covered": literal + other, "_other": other, "_subject": len(subj),
        "_sim": round(hit.similarity, 2), "_symbol": symbol_hit, "_path": path or file_hit or folder_hit, "_named": named[:3],
        "_pairs": held_pairs, "_kind": kind, "_intent": analysis.intent,
    }


def _anchor(sim: float, stats: PoolStats) -> float:
    """A candidate's similarity to the question as asked, relative to the pool (0-1): from min_sim to the best, never over
    a best below ANCHOR_FLOOR (a pool of noise stays noise)."""
    lo = stats.min_sim
    return max(0.0, sim - lo) / max(stats.max_sim - lo, ANCHOR_FLOOR - lo, 1e-9)


def _names_document(title: str, given: str) -> bool:
    """'docs/CONTRIBUTING.md' names the document titled 'CONTRIBUTING' (or 'CONTRIBUTING.md')."""
    base = given.rstrip("/").rsplit("/", 1)[-1]
    return bool(base) and title.casefold() in {base.casefold(), base.rsplit(".", 1)[0].casefold()}


def semantic(embedder) -> bool:
    """Whether an embedder's vectors carry meaning: a model's do; the hashing embedder's (offline, tests) are a second word
    match. An embedder may say so itself (a `semantic` attribute)."""
    flag = getattr(embedder, "semantic", None)
    if isinstance(flag, bool):
        return flag
    from app.services.embeddings import HashEmbedder

    return embedder is not None and not isinstance(embedder, HashEmbedder)


def score(f: dict, weights: dict[str, float] | None = None) -> float:
    return sum(w * float(f.get(name, 0.0)) for name, w in (weights or WEIGHTS).items())


def reasons(f: dict) -> list[str]:
    """Why a source ranked where it did, in our words: each line names a fact our code checked, never a model's words."""
    out = []
    if f.get("_symbol"):
        out.append(f"defines {f['_symbol']}")
    if f.get("path", 0) >= 1.0:
        out.append(f"is {f['_path']}, which the question names")
    elif f.get("path"):
        out.append(f"is in {f['_path'].rsplit('/', 1)[0]}/, the folder the question names")
    named = [n for n in f.get("_named", []) if n != f.get("_symbol")]
    if named and not f.get("path"):
        out.append("mentions " + ", ".join(named))
    if f.get("_subject") and f.get("_covered"):
        other = f" ({f['_other']} in another form)" if f.get("_other") else ""
        out.append(f"holds {f['_covered']} of {f['_subject']} subject words{other}")
    if f.get("_pairs"):
        out.append(f"has {f['_pairs']} of the question's phrases" if f["_pairs"] > 1 else "has a phrase of the question")
    if f.get("_sim", 0) > 0:
        out.append(f"{f['_sim']:.2f} similar to the question as asked")
    if f.get("_rank", 99) <= 3 and f.get("_size", 0) > 1:
        out.append(f"ranked {f['_rank']} of {f['_size']} across the searches")
    if f.get("asked"):
        out.append(f"{KIND_WORDS[f['_kind']]}, which the question asks about")
    elif f.get("prior", 0) >= 1.0 and f.get("_intent") in INTENT_WORDS:
        out.append(f"{KIND_WORDS[f['_kind']]} suits {INTENT_WORDS[f['_intent']]} question")
    if f.get("test"):
        out.append("a test file, and the question is not about tests")
    return out
