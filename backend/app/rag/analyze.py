"""The Planner's rules: what a question asks, and the searches that find it in the project's own words.

    analyze(question) -> Analysis

- intent: locate (where, which file), explain (what does, how does), howto (how do I, how to run), why, people (who owns,
  who explained), overview (what to read first, the architecture), or other. The intent sets how much each kind of source
  is worth (`priors`, used by the Judge) and how many of each the Retrievers fetch (`sizes`, factors on the rag_k_* settings).
  Question words typed in a hurry ("wich", "how dose the") are read as meant first (read_as_meant), for the intent and the
  subject only.
- identifiers: the names it gives, as written (text.identifiers).
- subject: the stems of the words it is about; `words` are those words as written.
- parts: a two-part question ("where is X defined, and how do I test it?") read as two.
- queries: at most MAX_RULE_QUERIES. queries[0] is always the question as asked, for every kind, weight 1.0: its similarity
  is the retrieval gate's evidence. Then the names, the parts, the subject words alone, and the subject expanded through a
  small thesaurus of the words software projects use for the same thing ("tests" -> pytest, spec; "install" -> setup,
  requirements, dependencies).
- alternates: other forms of each subject word that mean the same thing, which the light stemmer does not join: the verb
  of an agent noun or an action noun ("encoder" -> encode, "selection" -> select), the other spelling ("colour" / "color",
  "initialise" / "initialize"), the usual abbreviation ("parameter" -> param, "attribute" -> attr) and two adjacent words
  written as one ("time stamp" -> timestamp, "user name" -> username). The Judge counts a word held in another form
  (rerank.ALT_CREDIT), and the expanded query searches for them.
- asks_for: the kinds of source the question itself names ("in the KT recording", "according to the README").

Pure and fast: no I/O, nothing tuned to one corpus. The thesaurus, the word forms and the abbreviations are general
software-onboarding vocabulary."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from app.rag import text as T

Intent = Literal["locate", "explain", "howto", "why", "people", "overview", "other"]
Origin = Literal["asked", "names", "terms", "expand", "part", "model", "hypothetical"]
INTENTS: tuple[str, ...] = ("locate", "explain", "howto", "why", "people", "overview", "other")
KINDS: tuple[str, ...] = ("code", "doc", "meeting", "verified_answer")  # Chunk.source_type
MAX_RULE_QUERIES = 5
MAX_QUERY_CHARS = 2000  # a query is searched up to this many characters (the API takes questions of up to 2000)


@dataclass(frozen=True)
class Query:
    text: str
    kinds: tuple[str, ...] | None  # source types it runs against; None: every kind
    weight: float  # its lists' weight in the fusion
    origin: Origin


@dataclass(frozen=True)
class Analysis:
    question: str
    intent: Intent
    identifiers: list[str]
    subject: frozenset[str]
    parts: list[str]
    queries: list[Query]
    words: list[str] = field(default_factory=list)  # subject words as written, in order
    pairs: list[tuple[str, str]] = field(default_factory=list)  # adjacent subject stems: the question's phrases
    about_tests: bool = False  # the question asks about tests (test files are not penalised)
    priors: dict[str, float] = field(default_factory=dict)  # source type -> worth for this intent (0-1)
    sizes: dict[str, float] = field(default_factory=dict)  # source type -> factor on the configured result size
    alternates: dict[str, frozenset[str]] = field(default_factory=dict)  # subject stem -> stems of its other forms
    asks_for: frozenset[str] = frozenset()  # source types the question names ("in the KT call" -> meeting)

    def k_for(self, base: dict[str, int], kinds: list[str] | tuple[str, ...] | None = None) -> dict[str, int]:
        """Result size per kind: the configured size scaled by this intent's factor, at least 2."""
        return {kd: max(2, round(base.get(kd, 6) * self.sizes.get(kd, 1.0))) for kd in (kinds or KINDS)}


# ---------------------------------------------------------------- intent
# Who a question is about: "who" asks for a person unless code is its subject ("who calls check_labels" asks where it is
# called); "whose" is a person only before a people verb (not "the rule whose kind is ..."); "owner" only as "owner of",
# never as a field ("the owner field").
_PEOPLE_VERBS = (r"own|owns|owned|know|knows|knew|wrote|written|maintains?|maintained|built|explained|explains|reviews?|"
                 r"reviewed|approves?|approved|decides?|decided|runs the|is on call|should i|can i|do i|to ask|to contact")
_CODE_VERBS = (r"calls?|uses?|imports?|invokes?|triggers?|returns?|raises?|depends on|consumes?|subscribes? to|emits?|"
               r"references?|overrides?|implements?|inherits?|extends?|instantiates?")
# First match wins. "how do I" is a how-to before "how does" is an explanation; a bare verb ("run the tests") is a how-to only
# when nothing else matched, so "what does the run command do" stays an explanation.
_INTENT_RULES: list[tuple[str, re.Pattern]] = [
    ("people", re.compile(r"^\W*(?:who|whom)\b(?!\W+(?:" + _CODE_VERBS + r")\b)|"
                          r"\b(?:who|whom|whose)\W+(?:" + _PEOPLE_VERBS + r")\b|\bwho(?:'s| is| are) (?:the|our|an?) (?:owners?|"
                          r"maintainers?|experts?|contact)\b|\b(?:owners?|maintainers?) (?:of|for)\b|\b(?:codeowners|point of contact|"
                          r"reach out to|expert on|responsible for|ask about|talk to)\b|\bwhose (?:job|responsibility|turn|area|call)\b")),
    ("overview", re.compile(r"\b(?:overview|architecture|big picture|high[- ]level|read first|first read|start with|"
                            r"where (?:do|should|can|would) (?:i|we|you) (?:start|begin)|get(?:ting)? started|layout of|structure of|"
                            r"tour of|walk ?through|main (?:parts|components|modules|pieces)|"
                            r"what (?:is|does) (?:this|the) (?:project|repo|repository|codebase|service|app|system)\b)")),
    ("why", re.compile(r"^\W*why\b|\bwhy (?:do|does|did|is|are|was|were|we|not|would|should)\b|\b(?:rationale|motivation)\b|"
                       r"\breasons? (?:for|behind|why|that)\b|^\W*(?:what(?:'s| is| are| was) )?the reasons?\b")),
    ("locate", re.compile(r"^\W*who\W+(?:" + _CODE_VERBS + r")\b|\bwhere\b|\bwhich (?:file|files|function|functions|module|modules|class|classes|method|methods|folder|"
                          r"directory|package|part|line|test|tests|script|endpoint|route|handler)\b|"
                          r"\b(?:is|are) (?:defined|implemented|declared|located|configured)\b|\b(?:is there|are there|do we have)\b|"
                          r"\bfind the\b|\blocation of\b")),
    ("howto", re.compile(r"\bhow (?:do|can|should|would|could|shall) (?:i|we|you|one)\b|\bhow to\b|\bsteps? (?:to|for)\b|"
                         r"\bwhat(?:'s| is) the (?:command|way|process|procedure) (?:to|for)\b")),
    ("explain", re.compile(r"\b(?:what|how) (?:does|do|is|are|was|were|did)\b|\bwhat'?s\b|\bexplain\b|\bdescribe\b|"
                           r"\bwhat happens\b|\bmeaning of\b|\bpurpose of\b|\bdifference between\b|^\W*what\b|"
                           r"^\W*when\b|\bwhen (?:does|do|is|are|will|would|should|did|can)\b|"
                           r"\bhow (?:similar|long|many|much|often|big|large|small|fast|slow|soon|far|close|strict|old)\b")),
    ("howto", re.compile(r"\b(?:set ?up|install|configure|run|start|launch|deploy|build|add|create|enable|disable|fix|debug|"
                         r"test|upgrade|migrate|connect|use)\b")),
]

# Per intent: how much each kind of source is worth to the Judge (0-1)...
PRIORS: dict[str, dict[str, float]] = {
    "locate": {"code": 1.0, "doc": 0.6, "meeting": 0.5, "verified_answer": 0.7},
    "explain": {"code": 1.0, "doc": 0.8, "meeting": 0.8, "verified_answer": 0.9},
    "howto": {"code": 0.8, "doc": 1.0, "meeting": 0.7, "verified_answer": 1.0},
    "why": {"code": 0.7, "doc": 0.9, "meeting": 1.0, "verified_answer": 1.0},
    "people": {"code": 0.6, "doc": 0.7, "meeting": 1.0, "verified_answer": 0.9},
    "overview": {"code": 0.6, "doc": 1.0, "meeting": 0.9, "verified_answer": 0.7},
    "other": {"code": 0.9, "doc": 0.9, "meeting": 0.9, "verified_answer": 0.9},
}
# ...and how many of each the Retrievers fetch, as a factor on the rag_k_* setting.
SIZES: dict[str, dict[str, float]] = {
    "locate": {"code": 1.5, "doc": 0.7, "meeting": 0.7, "verified_answer": 0.7},
    "explain": {"code": 1.2, "doc": 1.0, "meeting": 1.0, "verified_answer": 1.0},
    "howto": {"code": 1.0, "doc": 1.5, "meeting": 1.0, "verified_answer": 1.2},
    "why": {"code": 0.8, "doc": 1.2, "meeting": 1.5, "verified_answer": 1.2},
    "people": {"code": 0.8, "doc": 1.0, "meeting": 1.5, "verified_answer": 1.0},
    "overview": {"code": 0.8, "doc": 1.5, "meeting": 1.2, "verified_answer": 0.8},
    "other": {"code": 1.0, "doc": 1.0, "meeting": 1.0, "verified_answer": 1.0},
}

# ---------------------------------------------------------------- the thesaurus
# Words software projects use for the same thing. A group whose word (or phrase) the question uses adds its other words to
# the expanded query. General vocabulary only: nothing here names one project's functions.
THESAURUS: tuple[tuple[str, ...], ...] = (
    ("test", "tests", "testing", "pytest", "unittest", "spec", "jest", "fixture", "assert"),
    ("run", "start", "launch", "execute", "command", "cli", "script", "invoke"),
    ("install", "setup", "set up", "dependencies", "requirements", "packages", "pip", "npm", "venv", "bootstrap"),
    ("config", "configuration", "configure", "settings", "env", "environment", "variables", "options", "dotenv"),
    ("error", "errors", "exception", "raise", "fail", "failure", "crash", "traceback", "bug"),
    ("auth", "authentication", "authorization", "login", "log in", "sign in", "sign-in", "signin", "token", "session",
     "credentials", "password", "oauth"),
    ("deploy", "deployment", "release", "ship", "production", "rollout", "ci", "pipeline"),
    ("database", "db", "postgres", "postgresql", "sql", "migration", "migrations", "schema", "table", "orm"),
    ("endpoint", "route", "routes", "api", "handler", "request", "http", "url", "controller"),
    ("owner", "owners", "maintainer", "codeowners", "responsible", "expert", "reviewer"),
    ("docs", "doc", "readme", "documentation", "guide", "contributing", "wiki"),
    ("default", "defaults", "fallback", "preset"),
    ("schedule", "scheduling", "calendar", "booking", "book", "meeting", "invite", "cron"),
    ("log", "logs", "logging", "logger", "trace", "tracing"),
    ("permission", "permissions", "access", "role", "roles", "rbac", "allow", "deny", "privileged"),
    ("validate", "validation", "validator", "check", "verify", "lint"),
    ("build", "compile", "bundle", "docker", "dockerfile", "image", "container", "makefile"),
    ("branch", "commit", "pull request", "pr", "merge", "review"),
    ("cache", "caching", "memo", "memoize"),
    ("queue", "job", "jobs", "worker", "workers", "background", "async"),
    ("webhook", "webhooks", "event", "events", "callback", "hook", "listener"),
    ("secret", "secrets", "key", "keys", "credential", "vault"),
    ("parse", "parser", "parsing", "load", "loader", "read", "deserialize"),
    ("add", "create", "insert", "new", "register"),
    ("delete", "remove", "drop", "purge"),
    ("update", "change", "modify", "edit", "patch", "mutate", "mutation"),
    ("label", "labels", "tag", "tags", "annotation", "metadata"),
    ("user", "users", "account", "member", "profile"),
    ("search", "query", "lookup", "find", "retrieve", "index"),
    ("send", "notify", "notification", "email", "mail", "message"),
    ("upload", "file", "blob", "storage", "bucket", "attachment"),
    ("rate limit", "ratelimit", "throttle", "quota", "limit"),
    ("monitor", "metrics", "alert", "health", "healthcheck", "observability"),
)


def _thesaurus_index() -> tuple[dict[str, int], list[tuple[str, int]]]:
    by_stem: dict[str, int] = {}
    phrases: list[tuple[str, int]] = []
    for i, group in enumerate(THESAURUS):
        for w in group:
            if " " in w or "-" in w:
                phrases.append((w, i))
            else:
                by_stem.setdefault(T.stem(w), i)
    return by_stem, phrases


_BY_STEM, _PHRASES = _thesaurus_index()
PER_GROUP, MAX_EXPANSION = 3, 8  # words one thesaurus group adds, and in all: more only dilutes the expanded query
# Words the sources that answer an overview or a people question use, whatever the question's own words.
INTENT_HINTS: dict[str, tuple[str, ...]] = {
    "overview": ("readme", "overview", "architecture", "introduction"),
    "people": ("owner", "codeowners", "maintainer"),
}
_TEST_STEMS = frozenset(T.stem(w) for w in THESAURUS[0])

# ---------------------------------------------------------------- other forms of a word
# Abbreviations code uses for a word a question spells out. General vocabulary, one meaning per group. Left out on purpose:
# abbreviations that are also common folder names (app, src, lib, pkg, util, doc): every file under such a folder would
# seem to hold the long word.
ABBREVIATIONS: tuple[tuple[str, ...], ...] = (
    ("org", "orgs", "organization", "organisation"), ("config", "cfg", "conf", "configuration"),
    ("auth", "authentication", "authorization", "authorisation"), ("repo", "repository"), ("env", "environment"),
    ("db", "database"), ("msg", "message"), ("info", "information"), ("init", "initialize", "initialise", "initialization"),
    ("admin", "administrator"), ("dir", "directory"), ("spec", "specification"), ("param", "parameter"), ("arg", "argument"),
    ("max", "maximum"), ("min", "minimum"), ("num", "number"), ("err", "error"), ("req", "request"), ("resp", "response"),
    ("ctx", "context"), ("tmp", "temp", "temporary"), ("prev", "previous"), ("dest", "dst", "destination"),
    ("stats", "statistics"), ("async", "asynchronous"), ("addr", "address"), ("attr", "attribute"), ("cmd", "command"),
    ("conn", "connection"), ("desc", "description"), ("exec", "execute"), ("expr", "expression"), ("idx", "index"),
    ("len", "length"), ("mgr", "manager"), ("obj", "object"), ("perm", "permission"), ("proc", "process"),
    ("prod", "production"), ("ref", "reference"), ("regex", "regexp", "regular expression"), ("str", "string"),
    ("val", "value"), ("var", "variable"), ("ver", "version"), ("k8s", "kubernetes"), ("pr", "pull request"),
    ("ci", "continuous integration"), ("kt", "knowledge transfer"), ("id", "identifier"), ("dev", "development"),
    ("tz", "timezone", "time zone"), ("i18n", "internationalization", "internationalisation"),
)
# Endings the light stemmer leaves alone, and the word they come from: "encoder" -> encode, "selection" -> select,
# "removal" -> remove, "failure" -> fail, "attachment" -> attach, "manually" -> manual. Each yields words to stem, never stems
# (the stemmer is not idempotent: "browse" stems to "brows", "brows" to "brow"). The first matching ending wins.
_FORMS: tuple[tuple[re.Pattern, tuple[str, ...]], ...] = (
    (re.compile(r"^(\w{3,}?)(?:er|ers|or|ors)$"), (r"\1", r"\1e")),
    (re.compile(r"^(\w{3,})(c|p)tions?$"), (r"\1\2t",)),
    (re.compile(r"^(\w{2,})letions?$"), (r"\1lete",)),
    (re.compile(r"^(\w{4,})ments?$"), (r"\1",)),
    (re.compile(r"^(\w{3,}[vsw])als?$"), (r"\1", r"\1e")),
    (re.compile(r"^(\w{3,})ures?$"), (r"\1", r"\1e")),
    (re.compile(r"^(\w{3,})ically$"), (r"\1ic",)),
    (re.compile(r"^(\w{4,})ly$"), (r"\1",)),
)
# British and American spellings of one word: initialise/initialize, colour/color, catalogue/catalog.
_SPELLINGS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"(?<=\w\w)([iy])s(e|es|ed|ing|er|ers|ation|ations)$"), r"\1z\2"),
    (re.compile(r"(?<=\w\w)([iy])z(e|es|ed|ing|er|ers|ation|ations)$"), r"\1s\2"),
    (re.compile(r"(?<=\w)(i|av|ol|on|ab|um|eighb)our(s?)$"), r"\1or\2"),
    (re.compile(r"(?<=\w)(i|av|ol|on|ab|um|eighb)or(s?)$"), r"\1our\2"),
    (re.compile(r"(?<=\w\w)logue(s?)$"), r"log\1"),
    (re.compile(r"(?<=\w\w)log(s?)$"), r"logue\1"),
)
# Each abbreviation group as (the stems of its single words, its multi-word phrases as token tuples).
_ABBREV: tuple[tuple[frozenset[str], tuple[tuple[str, ...], ...]], ...] = tuple(
    (frozenset(T.stem(w) for w in g if " " not in w), tuple(tuple(T.tokens(w)) for w in g if " " in w)) for g in ABBREVIATIONS)


def forms(word: str) -> list[str]:
    """Other forms of a word that mean the same thing, as words: its root ('encoder' -> encod, encode), its other spelling
    ('initialise' -> initialize) and that spelling's root. The caller stems them; a made-up form ('encod') stems like the
    real one or matches nothing."""
    w = word.lower()
    out: list[str] = []
    for s in [w] + [rx.sub(rep, w) for rx, rep in _SPELLINGS if rx.search(w)]:
        if s != w:
            out.append(s)
        for rx, reps in _FORMS:
            m = rx.match(s)
            if m:
                out += [m.expand(r) for r in reps]
                break
    return [x for x in dict.fromkeys(out) if x != w and len(x) >= 3]


def _contains(seq: list[str], phrase: tuple[str, ...]) -> bool:
    n = len(phrase)
    return n > 0 and any(tuple(seq[i:i + n]) == phrase for i in range(len(seq) - n + 1))


def _compounds(question: str, subject: set[str]) -> list[tuple[str, str, str]]:
    """Two adjacent subject words as one word ('time stamp' -> timestamp, 'user-name' -> username): (a, b, ab)."""
    seq = [w for w in T.words(question) if len(w) > 1]
    return [(a, b, a + b) for a, b in zip(seq, seq[1:]) if T.stem(a) in subject and T.stem(b) in subject and T.stem(a) != T.stem(b)]


def alternates_of(question: str, words: list[str]) -> dict[str, frozenset[str]]:
    """Subject stem -> the stems of its other forms: forms(), the abbreviation table (a phrase such as 'continuous
    integration' maps each of its words to 'ci'), and two adjacent subject words written as one (both words map to the
    compound's stems)."""
    subject = {T.stem(w) for w in words}
    out: dict[str, set[str]] = {}

    def add(s: str, alts) -> None:
        alts = {a for a in alts if a and a != s and a not in T.STOP}
        if alts:
            out.setdefault(s, set()).update(alts)

    for w in words:
        add(T.stem(w), {T.stem(f) for f in forms(w)})
    seq = T.tokens(question)
    for singles, phrases in _ABBREV:
        hit = [s for s in subject if s in singles] + [t for p in phrases if _contains(seq, p) for t in p if t in subject]
        for s in hit:
            add(s, singles)
    for a, b, ab in _compounds(question, subject):
        stems = {T.stem(ab)} | {T.stem(f) for f in forms(ab)}
        add(T.stem(a), stems)
        add(T.stem(b), stems)
    return {s: frozenset(alts) for s, alts in out.items()}


def alternate_words(question: str, words: list[str]) -> list[str]:
    """The alternates as words a search can use (forms, abbreviations, compounds), in the question's order, none with a
    subject word's own stem."""
    subject = {T.stem(w) for w in words}
    seq = T.tokens(question)
    out: list[str] = []
    for w in words:
        out += forms(w)
    for g, (singles, phrases) in zip(ABBREVIATIONS, _ABBREV):
        if subject & singles or any(_contains(seq, p) for p in phrases):
            out += [x for x in g if " " not in x]
    for a, b, ab in _compounds(question, subject):
        out += [ab] + forms(ab)
    have, keep = set(subject), []
    for x in out:
        s = T.stem(x)
        if s not in have and s not in T.STOP:
            have.add(s)
            keep.append(x)
    return keep


# ---------------------------------------------------------------- the kind of source a question names
ASKS_FOR: tuple[tuple[str, re.Pattern], ...] = (
    # not bare "meeting" or "transcript": code that books meetings or cleans transcripts is asked about in those words too
    ("meeting", re.compile(r"\b(?:kt|knowledge[- ]transfer|recorded|recordings?|walk-?throughs?|"
                           r"(?:kt|onboarding|handover) (?:calls?|sessions?|meetings?))\b")),
    ("doc", re.compile(r"\b(?:readme|docs|documentation|documented|wiki|handbook|runbook|playbook|changelog|"
                       r"(?:contributing|style|setup|install(?:ation)?|deploy(?:ment)?) guide)\b")),
    ("verified_answer", re.compile(r"\b(?:verified answers?|faqs?)\b")),
)
ASKED_SIZE = 1.5  # the result-size factor, at least, for a kind of source the question names


def asks_for(question: str) -> frozenset[str]:
    """The kinds of source a question names: 'what did the KT call say' wants a KT call, 'according to the README' a document."""
    q = " ".join(question.lower().split())
    return frozenset(kind for kind, rx in ASKS_FOR if rx.search(q))


# A second question inside the first: split at "?" or ";", or at ", and"/"and also"/"also" before a question word.
_PART_SPLIT = re.compile(r"[?;]+\s*|\s*,?\s+(?:and|also|plus|then)\s+(?:also\s+)?(?=(?:how|what|where|why|who|which|when|is|are|does|do|"
                         r"can|should|could)\b)", re.I)


# Question words typed in a hurry, read as meant (for the intent and the subject only: queries[0] stays as asked). Words
# that are real words too ("were", "dose", "wat") are read as a question word only where nothing else fits.
_QUESTION_TYPOS = {"wich": "which", "whcih": "which", "wihch": "which", "whihc": "which", "waht": "what", "whta": "what",
                   "hwo": "how", "deos": "does", "doese": "does", "wher": "where", "whre": "where", "wehre": "where"}
_IN_CONTEXT: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"^(\W*)were (?=(?:is|are|do|does|can|should|did)\b)", re.I), r"\1where "),  # "were are X rejected"
    (re.compile(r"\b(how|why|where|when|what) dose (?=(?:the|a|an|it|this|that|we|i|you|they|my|our|your)\b)", re.I),
     r"\1 does "),  # "how dose the X work"; "how is the dose set" keeps its dose
    (re.compile(r"^(\W*)wat (?=(?:is|are|does|do|did|was|were)\b)", re.I), r"\1what "),
)


def read_as_meant(question: str) -> str:
    """The question with misspelt question words fixed ('wich' -> which, 'how dose' -> how does, 'were are' -> where are), so
    the intent rules and the subject see what the asker meant. Only whole words; nothing else changes."""
    q = question
    for rx, rep in _IN_CONTEXT:
        q = rx.sub(rep, q)
    return re.sub(r"[A-Za-z]+", lambda m: _QUESTION_TYPOS.get(m.group(0).lower(), m.group(0)), q)


_PROJECT_WORDS = re.compile(r"\b(?:project|repo|repository|codebase|service|app|application|system|platform)\b")


def intent_of(question: str) -> Intent:
    q = " ".join(read_as_meant(question).lower().split())
    named = bool(T.identifiers(question))
    for intent, rx in _INTENT_RULES:
        if rx.search(q):
            if intent == "overview" and named and not _PROJECT_WORDS.search(q):
                continue  # "the architecture of check_labels": a question about that name, not a tour of the project
            return intent  # type: ignore[return-value]
    return "other"


def parts_of(question: str) -> list[str]:
    """The question's parts when it asks two or three things, each with some subject; [] when it asks one."""
    pieces = [p.strip(" ,.") for p in _PART_SPLIT.split(question) if p and p.strip(" ,.")]
    pieces = [p for p in pieces if T.subject_words(p)]
    return pieces[:3] if len(pieces) >= 2 else []


def expansion(question: str, words: list[str], intent: str = "other") -> list[str]:
    """Other words for what the question names (at most MAX_EXPANSION in all): for an overview or people question the words
    such sources use, then the subject words' other forms (alternate_words), then the first few words of each thesaurus
    group the question touches."""
    low = " " + " ".join(T.words(question)) + " "
    groups: list[int] = []
    for w in words:
        g = _BY_STEM.get(T.stem(w))
        if g is not None and g not in groups:
            groups.append(g)
    for phrase, g in _PHRASES:
        if f" {' '.join(T.words(phrase))} " in low and g not in groups:
            groups.append(g)
    have = {T.stem(w) for w in words}
    out: list[str] = []

    def take(w: str) -> bool:
        s = T.stem(w.replace(" ", "").replace("-", ""))
        if s in have or all(t in have for t in T.tokens(w)):  # 'pull request' adds nothing to a question that says it
            return False
        have.add(s)
        out.append(w)
        return True

    for w in INTENT_HINTS.get(intent, ()):
        take(w)
    for w in alternate_words(question, words):
        take(w)
    for g in groups:
        added = 0
        for w in THESAURUS[g]:
            if added >= PER_GROUP:
                break
            added += take(w)
    return out[:MAX_EXPANSION]


def _key(text: str) -> str:
    """Two queries are the same search when they have the same words: 'check_labels' and 'check labels' are."""
    return " ".join(T.words(text))


def analyze(question: str) -> Analysis:
    """Read the question (see the module docstring). Pure: the same question always gives the same Analysis."""
    q = " ".join((question or "").split())
    meant = read_as_meant(q)
    intent = intent_of(q)
    names = T.identifiers(q)
    words = T.subject_words(meant)
    stems = [T.stem(w) for w in words]
    seq = T.tokens(q)
    subj = set(stems)
    pairs = list(dict.fromkeys((a, b) for a, b in zip(seq, seq[1:]) if a in subj and b in subj and a != b))
    parts = parts_of(q)
    about_tests = bool(subj & _TEST_STEMS) or any(T.is_test_path(n) for n in names)

    queries: list[Query] = [Query(q[:MAX_QUERY_CHARS], None, 1.0, "asked")]
    seen = {_key(queries[0].text)}

    def add(text: str, weight: float, origin: Origin) -> None:
        text = " ".join(text.split())[:MAX_QUERY_CHARS]
        if text and _key(text) not in seen:
            seen.add(_key(text))
            queries.append(Query(text, None, weight, origin))

    if names:
        add(" ".join(names), 1.0, "names")
    for p in parts[:2]:
        add(p, 0.7, "part")
    if words:
        add(" ".join(words), 0.8, "terms")
    # the names' own words are exact: expanding them ("check" -> verify, lint) only adds noise
    named = {T.stem(w) for n in names for w in T.words(n)}
    extra = expansion(q, [w for w in words if T.stem(w) not in named], intent)
    if extra:
        add(" ".join(words + extra), 0.5, "expand")
    for p in parts[2:]:
        add(p, 0.7, "part")
    wanted = asks_for(q)
    sizes = dict(SIZES[intent])
    for kind in wanted:  # the question names this kind of source: fetch more of it
        sizes[kind] = max(sizes.get(kind, 1.0), ASKED_SIZE)
    return Analysis(question=q, intent=intent, identifiers=names, subject=frozenset(subj), parts=parts,
                    queries=queries[:MAX_RULE_QUERIES], words=words, pairs=pairs, about_tests=about_tests,
                    priors=dict(PRIORS[intent]), sizes=sizes, alternates=alternates_of(q, words), asks_for=wanted)
