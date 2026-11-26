"""Tiered answer routing (POST /api/ask/route; the Guide, app/agents/specialists/guide/agent.py). A question goes to the
cheapest tier that can answer it honestly, and to a person only when the asker asks for one:

  1. AI answer      the research crew (app/rag/crew.py) gathers the evidence, the model answers from it, and two gates
                    hold: retrieval (answer_gate: the question names an identifier - a path, a snake_case or camelCase
                    name - that the cited sources have; or the best cited source is at least 0.55 similar to the question
                    as asked AND the sources hold a third of its subject words; or they hold most of its subject words,
                    all of them for a short question; never when the question names things no cited source has), and
                    the citation check (every sentence is supported by ONE cited source, and its [n] points at a source
                    that supports it). The model's own confidence is shown, never trusted. `writer` says who wrote the
                    answer: a model, or the offline writer quoting the sources ("extractive"), which also answers when
                    the AI is off on the server. The chat (app/agents/specialists/guide/qa.py) answers through the same function.
  2. Related        a verified answer or an answered expert question with similarity >= 0.80. Shown without names, and never
                    with another hire's words: a verified answer shows the question as its verifier wrote it; an expert's
                    answer shows a de-identified paraphrase (the subject words of the question).
  -  Offer          neither: nothing is sent to anyone. The answer offers "Ask a person" (escalation available).
  3. Mentor         the asker asked for a person: their mentor, with the AI draft attached to the request.
  4. Domain owner   escalated past the mentor, or no mentor: the CODEOWNERS owner of the code the answer CITED (else the
                    best-ranked code found), never the catch-all '*' while a specific rule exists; else whoever explained
                    that code most recently on a KT call; else the project's owner of last resort. The draft goes with it.

The citation check is deterministic. The answer is split into sentences (code blocks are sentences too). A sentence that
makes a claim must be supported by one cited source on its own: every identifier it names and every number in it appear
in that source, at least half of its subject words (stemmed, stop words removed) do, and its negation agrees: a sentence
that says never/not/no is supported only by a source that negates the same thing, and a plain one only by a source that
does not. A sentence with no identifier, number or subject word makes no claim. Without a model, the offline writer
(app/agents/core/llm.py) quotes each sentence from one cited source and keeps only sentences this check supports; the opening
notice of its earlier answers ("Offline demo answer ...") is not a claim.

Similarity for tier 2 is deterministic too: 1.0 for the same normalised question, else the cosine of the two questions'
subject-word sets. It needs no embedding call, so it works with the AI off.

Expert time, one path for every hand-off to a person (this, and the chat's, app/agents/specialists/guide/qa.py): a question interrupts its
person now only within their office hours (when they set any) and under their daily cap (`max_interruptions_per_day`, 3 by
default); otherwise it waits in their digest, which the daily tick sends to everyone with `digest` on as one notification.
`urgent` is the asker's to use once a day, and only reaches a mentor at once (tier 3); an urgent question past that, or to a
domain owner, follows the same rules as any other.

The question, the sources and the model's output are data: the question reaches the model only inside the data block. Who a
question goes to comes from the program (the mentor), CODEOWNERS and the KT calls, never from the question, and nothing here
changes a role or an approval. The agentic Guide (app/agents/specialists/guide/agent.py) may SUGGEST the domain owner from the evidence its
tools found (who_knows, code_owners, kt_moments): the suggestion is used at tier 4 only if `suggested_target` finds that
person eligible (an active expert, manager or admin on the project, not the asker), and the mentor still comes first."""
from __future__ import annotations

import dataclasses
import logging
import math
import re
from datetime import datetime, timezone

from app.agents.core import prompts
from app.agents.core.citations import label_for
from app.agents.core.llm import LLMError, OfflineLLM, _near, _stem, _subject, _words, embed_data, meant
from app.agents.core.schemas import Chunk, Citation, Hit, QADraft, QAResult
from app.agents.specialists.guide import qa
from app.lifecycle.context import Ctx
from app.ports import AIUnavailable, User, norm_question
from app.rag import crew
from app.rag import text as T
from app.rag.analyze import read_as_meant
from app.services.chunking import module_of
from app.services.codeowners import CATCH_ALL, parse_codeowners, rule_for
from app.services.knowledge import Speakers

MIN_SIMILARITY = 0.55  # retrieval: the best cited source's similarity to the question (PLAN §5)
MIN_CONFIDENCE = MIN_SIMILARITY  # the old name of the gate
MIN_SUPPORT = 0.5  # share of a sentence's subject words its supporting source must contain
MIN_TERMS = 0.5  # the retrieval gate's third rule: the cited sources together hold this share of the question's subject words
# (at least this share after the agent loop, whose VERIFY then checks every claim; more than it on the deterministic path,
# which has no VERIFY: on the demo's golden set "at least half" let "How do I deploy the policy engine with Helm?" through)
RELATED_SIMILARITY = 0.80
MAX_RELATED = 3
URGENT_PER_DAY = 1  # urgent questions an asker may send at once, per day (to their mentor only)
NOTICE = re.compile(r"^\s*offline demo answer\b[^.]*\.\s*", re.I)
IDENT = re.compile(r"[A-Za-z_][\w-]*(?:[./][\w-]+)+|\b[a-z][a-z0-9]*_[a-z0-9_]+\b|\b[a-z]+[A-Z][A-Za-z0-9]*\b")
SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z`\"'(\[])|\n\s*\n")
NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])")
NEGATION = re.compile(r"\b(?:not|never|no|none|cannot|can't|doesn't|don't|isn't|aren't|won't|without|neither|nor)\b", re.I)
# Python's None is a value, not the word "none": "... if False else None" says nothing negative. Read before NEGATION.
NONE_LITERAL = re.compile(r"(?:\bis\s+not\s+|\bis\s+|[!=]=\s*)?\bNone\b")
UNIT = re.compile(r"\n|(?<=[.!?])\s+")
PERSON_ROLES = frozenset({"expert", "manager", "admin"})
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def terms(text: str) -> set[str]:
    return _subject(text or "")


def sentences(answer: str) -> list[str]:
    return [p.strip() for p in SPLIT.split(NOTICE.sub("", answer or "", count=1)) if p.strip()]


def _negated(text: str) -> bool:
    return bool(NEGATION.search(NONE_LITERAL.sub(" ", text)))


def _polarity_agrees(sentence: str, words: set[str], ids: set[str], body: str) -> bool:
    """Does the source say the opposite of the sentence? Only the source's lines about the same thing decide: lines that
    share the sentence's identifiers, or at least three (or half) of its subject words. With no such line there is no
    evidence of a contradiction for a positive claim; a negative claim must find its negative stated. (Judging by the
    whole source was wrong: nearly every file has a "not" somewhere.)"""
    neg = _negated(sentence)
    covering = []
    for unit in (u for u in UNIT.split(body) if u.strip()):
        shared = len(words & terms(unit))
        if (ids and any(i in unit for i in ids)) or (words and (shared >= 3 or shared / len(words) >= MIN_SUPPORT)):
            covering.append(unit)
    if neg:  # a negative claim ("never", "does not") needs a line of the source that says the negative itself
        return any(_negated(u) for u in covering)
    return not covering or any(not _negated(u) for u in covering)


CITE_MARK = re.compile(r"\s*[\[(](?:\d+\s*(?:[,–-]\s*)?)+[\])]")  # "[1, 2]", "(3)", "[4-6]": the answer pointing at its sources
ENUM = re.compile(r"(?m)^\s*(?:[-*•]|\(?\d{1,2}[.)]|[A-Za-z][.)])(?:\s+|$)")  # list markers: "1.", "2)", "A.", "-"
STEP_NUM = re.compile(r"\b\d{1,2}\)\s")  # inline step numbers: "1) do this, 2) then that"
TRAILING_ENUM = re.compile(r"\s+(?:\d{1,2}|[A-Z])[.)]\s*$")  # "...as follows: 1." or "in order: A." (the next item's marker)
NOT_IDENT = frozenset({"e.g", "i.e", "etc", "vs"})
NUM = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")  # "3.0m" states 3.0; "r0" states no number


def _nums(text: str) -> set[str]:
    """The numbers a text states, compared by value: "3.0", "3" and "3.00" are the same number."""
    return {format(float(n), "g") for n in NUM.findall(text)}
MIN_SUPPORT_UNION = 0.6  # several cited sources together, when none holds MIN_SUPPORT alone...
MIN_SUPPORT_EACH = 0.35  # ...and the best of them still holds this much


def _claim_text(sentence: str) -> str:
    """The sentence as a claim: citation markers and list numbering removed, since they state nothing about the code."""
    text = STEP_NUM.sub(" ", ENUM.sub("", CITE_MARK.sub("", sentence)))
    return TRAILING_ENUM.sub("", text).strip()


def _supports(sentence: str, bodies: list[str], body_terms: list[set[str]]) -> list[int]:
    """The cited sources (numbered from 1) that support this sentence; [] when it is not supported.

    Strict where a wrong answer hurts, lenient on wording:
    - every identifier it names (a path, a function, a snake_case or camelCase name) is in some cited source;
    - its subject words: one source holds MIN_SUPPORT of them, or the sources together MIN_SUPPORT_UNION with the best
      holding MIN_SUPPORT_EACH (an answer may combine two files in one sentence);
    - every number it states is in a relevant source (one with its identifiers or MIN_SUPPORT_EACH of its words);
    - it says the same thing as those sources, not the opposite (negation agrees).
    """
    claim = _claim_text(sentence)
    ids = {i for i in IDENT.findall(claim) if i.lower() not in NOT_IDENT}
    words, nums = terms(claim), _nums(claim)
    share = [len(words & t) / len(words) if words else 1.0 for t in body_terms]
    # every name it uses comes from a cited source that is about this claim: that catches one function's behaviour
    # pinned on another ("check_labels retries three times" when only send() retries), while still allowing a sentence
    # that names two scripts from two files
    if any(not any(i in b and sh >= MIN_SUPPORT_EACH for b, sh in zip(bodies, share)) for i in ids):
        return []
    union = len(words & set().union(*body_terms)) / len(words) if words and body_terms else 1.0
    best = max(share, default=0.0)
    specific = bool(ids or nums)  # names a file, function or number: checked strictly; plain prose only needs to agree
    if specific and not (best >= MIN_SUPPORT or (union >= MIN_SUPPORT_UNION and best >= MIN_SUPPORT_EACH)):
        return []
    if not specific and best < MIN_SUPPORT_EACH:
        return []
    relevant = [n for n, (b, sh) in enumerate(zip(bodies, share)) if sh >= MIN_SUPPORT_EACH or (ids and any(i in b for i in ids))]
    if not relevant:
        return []
    if nums and not nums <= {x for n in relevant for x in _nums(bodies[n])}:
        return []
    if not any(_polarity_agrees(claim, words, ids, bodies[n]) for n in relevant):
        return []
    return [n + 1 for n in relevant if share[n] >= MIN_SUPPORT_EACH or ids]


def citation_check(answer: str, sources: list[Chunk]) -> dict:
    """{"ok", "sentences": [{text, claim, sources: [n]}]}: n numbers the cited sources from 1, listing the sources that
    support the sentence. ok needs at least one claim, and every claim supported (see _supports)."""
    bodies = [f"{c.content}\n{label_for(c)}" for c in sources]
    body_terms = [terms(b) for b in bodies]
    parts = []
    for s in sentences(answer):
        text = _claim_text(s)
        claims = bool(re.search(r"[A-Za-z]{3}", text)) and bool(IDENT.findall(text) or len(terms(text)) >= 2 or _nums(text))
        parts.append((s, claims, text.endswith(":")))
    # a lead-in ("run_all.sh runs these steps in order:") introduces a list whose items are the claims; it only counts
    # as a claim itself when nothing that follows it does
    followed = any(c and not lead for _, c, lead in parts)
    rows = []
    for s, claims, lead in parts:
        claim = claims and not (lead and followed)
        rows.append({"text": s[:240], "claim": claim, "sources": _supports(s, bodies, body_terms) if claim else []})
    ok = any(r["claim"] for r in rows) and all(r["sources"] or not r["claim"] for r in rows)
    return {"ok": ok, "sentences": rows}


def similarity(a: str, b: str) -> float:
    if norm_question(a) == norm_question(b):
        return 1.0
    ta, tb = terms(a), terms(b)
    return len(ta & tb) / math.sqrt(len(ta) * len(tb)) if ta and tb else 0.0


def retrieval_gate(question: str, cited) -> dict:
    """{"ok", "similarity", "rule", "names"}: the best cited hit's similarity, and the identifier rule for keyword matches."""
    best = max((h.similarity for h in cited), default=0.0)
    if best >= MIN_SIMILARITY:
        return {"ok": True, "similarity": round(best, 2), "rule": "similarity", "names": []}
    names = named_in(question, cited)
    return {"ok": bool(names), "similarity": round(best, 2), "rule": "identifier" if names else None, "names": names}


def given_names(question: str) -> list[str]:
    """The identifiers a question gives (a path, a snake_case or camelCase name), without the abbreviations IDENT also matches
    ("e.g", "i.e") and slash-words that are English, not paths ("and/or", "read/write")."""
    return sorted(i for i in set(IDENT.findall(question))
                  if i.lower() not in T.NOT_IDENT and not ("/" in i and not T._plausible_path(i)))


def holds_name(name: str, text: str) -> bool:
    """The text has the name as a whole token: "e.g" is not in "resource.get(", nor "run" in "rerun"."""
    return re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text) is not None


def _is_file(name: str) -> bool:
    return bool(T._FILE_EXT.search(name)) and not name.endswith("/")


def _is_that_file(name: str, cited) -> bool:
    """A cited source is the file itself ('policies/default.yaml' is answered by that file, not by a line that opens it)."""
    want = name.lstrip("./")
    return any((p := str(h.chunk.ref.get("path") or "")) == want or p.endswith("/" + want) for h in cited)


def named_in(question: str, cited) -> list[str]:
    """The identifiers the question gives that the cited sources have: a code name as a whole token of a cited source, a
    file as a cited source of its own."""
    texts = [f"{h.chunk.content}\n{label_for(h.chunk)}" for h in cited]
    return [i for i in given_names(question)
            if (_is_that_file(i, cited) if _is_file(i) else any(holds_name(i, t) for t in texts))]


def names_missing(question: str, cited, paths=None) -> list[str]:
    """The names the question gives that no cited source has: the sources are about something else, or the thing does not
    exist (a false premise: "initContainers", a file that is not in the repository). A code name counts as had when a cited
    source has it as a token, even misspelt by a letter or two ("chek_privilaged"); a dotted name when it has each part
    ("status.message" in '"status": {"message": ...}'); a file when it is cited, or when a cited source mentions it and it
    is a file of the project (`paths`, the index's files; None: any mention)."""
    names = given_names(question)
    if not names:
        return []
    texts = "\n".join(f"{h.chunk.content}\n{label_for(h.chunk)}" for h in cited)
    tokens = {t.lower() for t in re.findall(r"[A-Za-z_][\w\-]*", texts)}
    out = []
    for n in names:
        if _is_file(n):
            want = n.lstrip("./")
            exists = paths is None or any(p == want or p.endswith("/" + want) for p in paths)
            if _is_that_file(n, cited) or (exists and holds_name(n, texts)):
                continue
        elif holds_name(n, texts):
            continue
        elif "." in n and "/" not in n and all(part.lower() in tokens for part in n.split(".") if part):
            continue
        else:
            limit = 2 if len(n) >= 8 else 1 if len(n) >= 5 else 0
            if limit and any(abs(len(t) - len(n)) <= limit and _near(n.lower(), t, limit) for t in tokens):
                continue
        out.append(n)
    return out


def subject_of(question: str) -> set[str]:
    """The question's subject words, with its misspelt question words read as meant ("wich", "hwo")."""
    return terms(read_as_meant(question))


def held_share(question: str, text: str) -> tuple[float, set[str], set[str]]:
    """(the share of the question's subject words the text holds, its subject words, the ones it does not hold): a word
    counts as held in another form of it or misspelt by a letter or two (llm.meant), so "testss", "rnu" and "initialise"
    meet "tests", "run" and "initialize"."""
    words, held = meant(read_as_meant(question), text)
    return (len(held) / len(words) if words else 0.0), words, words - held


MIN_TERMS_SHORT = 3  # a question of at most this many subject words passes the terms rule only when the sources hold them all


def _participles(question: str) -> set[str]:
    """The question's passive verbs ("Where are missing labels REPORTED?", "where is it DEFINED"), stemmed: they say what
    happens to the subject, not what the subject is, so a short question is not refused for a source that words its
    verb otherwise. Nouns ending in -ed are short or end in -eed ("seed", "speed")."""
    return {_stem(w) for w in _words(read_as_meant(question)) if len(w) >= 6 and w.endswith("ed") and not w.endswith("eed")}
MIN_TERMS_WITH_SIMILARITY = 1 / 3  # the similarity rule's own floor: the cited sources still hold this share of the subject


def answer_gate(question: str, cited, *, verified: bool = False, paths=None) -> dict:
    """The Guide's retrieval gate on the sources an answer cites, one rule set for the agent loop and the deterministic path.
    It passes on one of three rules:

    - "identifier": the question gives a name (a path, a snake_case or camelCase name) that a cited source has as a token;
    - "similarity": the best cited source is at least MIN_SIMILARITY similar to the question AS ASKED (Hit.similarity,
      whichever search found it) and the cited sources hold at least MIN_TERMS_WITH_SIMILARITY of its subject words. A real
      embedding model puts an unanswerable question about the project above 0.55 too (0.48-0.68 for gemini-embedding-2 on
      the demo), so similarity alone is not evidence that the sources answer it;
    - "terms": the cited sources hold the question's subject words: at least MIN_TERMS of them when VERIFY checks the claims
      afterwards (`verified`, the agent loop), more than MIN_TERMS otherwise, and then all of them for a question of at most
      MIN_TERMS_SHORT words: in "What is the admission webhook's SLA?" the one word missing is the whole question (a
      passive verb may be missing: "Where are missing labels reported?").

    None of them passes when the question gives names (paths, snake_case or camelCase names) and the cited sources have
    none of them (names_missing): "How does the engine check initContainers?" is not answered by a source about containers.

    A word is held in another form or misspelt (held_share). A source only an exact-name lookup found has similarity 0.0:
    it passes through the identifier rule or the subject words, never through similarity. The citation check still decides
    every sentence."""
    best = max((h.similarity for h in cited), default=0.0)
    names = named_in(question, cited)
    share, words, missing = held_share(question, "\n".join(f"{h.chunk.content}\n{label_for(h.chunk)}" for h in cited))
    out = {"ok": False, "similarity": round(best, 2), "rule": None, "names": names}
    if not names and given_names(question) and len(names_missing(question, cited, paths)) == len(given_names(question)):
        return out  # it names things and the sources have none of them: they cannot be what it asks about
    if best >= MIN_SIMILARITY and (not words or share >= MIN_TERMS_WITH_SIMILARITY):
        return {**out, "ok": True, "rule": "similarity", "names": []}
    if names:
        return {**out, "ok": True, "rule": "identifier"}
    if not words:
        return out
    if verified:
        enough = share >= MIN_TERMS
    else:
        enough = share > MIN_TERMS and not (missing - _participles(question) and len(words) <= MIN_TERMS_SHORT)
    return {**out, "ok": True, "rule": "terms"} if enough else out


def writer_of(llm) -> str:
    """Who wrote a deterministic answer: the offline writer quotes the cited sources ("extractive"; no model on the server),
    anything else is a model. The one place this is decided (an agent loop's answer is always a model's)."""
    return "extractive" if isinstance(llm, OfflineLLM) else "model"


GATE_HOW = {"similarity": "the best is {sim:.2f} similar to the question (at least {min}) and they hold its subject words",
            "identifier": "the question names {names}, which the cited source contains",
            "terms": "the cited sources hold the question's subject words"}


def gate_refusal(gate: dict) -> str:
    if gate["similarity"] >= MIN_SIMILARITY:
        return (f"Not enough evidence: the best cited source is {gate['similarity']:.2f} similar to the question, but the cited "
                "sources hold too few of its subject words, and the question names nothing they contain.")
    return (f"Not enough evidence: the best cited source is {gate['similarity']:.2f} similar to the question (below "
            f"{MIN_SIMILARITY}), the question names nothing it contains, and the cited sources hold too few of its subject words.")


# [n] markers: not "items[0]" (a name before it) nor "x[1][2]"; "[1-3]" is 1, 2 and 3
CITE = re.compile(r"(?<![\w\]\[])\[(\d+(?:\s*[,–-]\s*\d+)*)\]")
CODE_SPAN = re.compile(r"```.*?```|``[^\n]*?``|`[^`\n]*`", re.S)  # code the answer quotes: its [0] or [1] is an index, not a citation
MAX_RANGE = 20  # a marker range wider than this ("[1-500]") is read as its two ends


def marker_numbers(marker: str) -> list[int]:
    """The source numbers a marker's inside names: "1, 3" -> [1, 3]; "2-4" -> [2, 3, 4]."""
    nums: list[int] = []
    for part in re.split(r"\s*,\s*", marker.strip()):
        m = re.fullmatch(r"(\d+)\s*[–-]\s*(\d+)", part)
        if m:
            a, b = int(m[1]), int(m[2])
            nums += list(range(a, b + 1)) if a <= b and b - a <= MAX_RANGE else [a, b]
        elif part.isdigit():
            nums.append(int(part))
    return list(dict.fromkeys(nums))


def _in_prose(text: str, fn) -> str:
    """fn applied to the text outside its code spans; code is left as written."""
    out, at = [], 0
    for m in CODE_SPAN.finditer(text):
        out += [fn(text[at:m.start()]), m.group(0)]
        at = m.end()
    return "".join(out + [fn(text[at:])])


def renumber(text: str, order: list[int]) -> str:
    """The answer's [n] markers renumbered to the citations' order ([1] is the first citation, the source numbered order[0]);
    markers of sources it does not cite are dropped. A model, and the offline writer, number sources as they were shown.
    Code spans are left as written."""
    new = {n: i + 1 for i, n in enumerate(order)}

    def one(m):
        nums = [new[x] for x in marker_numbers(m.group(1)) if x in new]
        return f"[{', '.join(str(x) for x in dict.fromkeys(nums))}]" if nums else ""

    def prose(part: str) -> str:
        part = re.sub(r"(?<=\S) {2,}(?=\S)", " ", CITE.sub(one, part))  # a dropped marker leaves no double space
        return re.sub(r" +([.,;:])", r"\1", part)

    return _in_prose(text, prose).strip()


def markers_in(sentence: str) -> set[int]:
    """The source numbers a sentence's markers name (outside its code)."""
    found: set[int] = set()

    def read(part: str) -> str:
        for m in CITE.finditer(part):
            found.update(marker_numbers(m.group(1)))
        return part

    _in_prose(sentence, read)
    return found


def _with_marker(sentence: str, n: int) -> str:
    """The sentence pointing at source n alone: its own markers dropped, [n] before its closing punctuation."""
    bare = _in_prose(sentence, lambda part: re.sub(r" +([.,;:!?])", r"\1", CITE.sub("", part))).rstrip()
    if bare.endswith("```"):
        return f"{bare} [{n}]"
    m = re.search(r"[.!?:]+[\"')\]]*$", bare)
    return f"{bare[:m.start()].rstrip()} [{n}]{bare[m.start():]}" if m else f"{bare} [{n}]"


def align_markers(answer: str, sources: list[Chunk], numbers: list[int]) -> str:
    """Every claim's [n] pointing at a source that supports it. numbers[i] is the [n] the answer uses for sources[i]. A
    claim whose markers name none of its supporting sources (or that has none) gets the marker of the first source that
    supports it, so a citation chip never points at a source that does not say what the sentence says. A sentence no
    source supports is left as it is: the citation check refuses it."""
    bodies = [f"{c.content}\n{label_for(c)}" for c in sources]
    body_terms = [terms(b) for b in bodies]
    parts = re.split(f"({SPLIT.pattern})", NOTICE.sub("", answer or "", count=1))
    out = []
    for i, part in enumerate(parts):
        if i % 2 or not part.strip():
            out.append(part)
            continue
        text = _claim_text(part)
        claims = bool(re.search(r"[A-Za-z]{3}", text)) and bool(IDENT.findall(text) or len(terms(text)) >= 2 or _nums(text))
        support = _supports(part, bodies, body_terms) if claims and not text.endswith(":") else []
        good = {numbers[k - 1] for k in support if 0 < k <= len(numbers)}
        if good and not (markers_in(part) & good):
            lead = len(part) - len(part.lstrip())
            part = part[:lead] + _with_marker(part.strip(), numbers[support[0] - 1]) + part[len(part.rstrip()):]
        out.append(part)
    return "".join(out)


def _code_paths(chunks) -> list[str]:
    return list(dict.fromkeys(c.ref["path"] for c in chunks if c.source_type == "code" and c.ref.get("path")))


# ---------------------------------------------------------------- tier 1
log = logging.getLogger("lifecycle.routing")
SEARCH_DOWN = "Semantic search is unavailable right now: the evidence was found by keyword and by the names the question gives."


class _NoEmbedding:
    """The index without its embedding search, for when the crew's own search raised (no embedder at all: the AI is off,
    AIUnavailable): the keyword channel where the index has one (search_grid(..., semantic=False)), else nothing, so the
    crew's exact lookups of the files and functions the question names are what remains. Read-only, like the index."""

    def __init__(self, index):
        self._index = index

    def __getattr__(self, name):
        return getattr(self._index, name)

    def search(self, project_id, query, k=8, source_types=None):
        return []

    def search_grid(self, project_id, queries, kinds, k, **_):
        try:
            return self._index.search_grid(project_id, queries, kinds, k, semantic=False)
        except TypeError:  # an index with no keyword-only search
            return [{kd: [] for kd in kinds} for _ in queries]


def gather(deps, project_id: str, question: str, *, k: int = 8, steps=None, on_step=None):
    """(the research crew's Retrieval, degraded): the crew's evidence for an answer. The crew itself falls back to keyword
    search when the question's embedding fails (Retrieval.degraded); when its search raises anyway (no embedder at all), it
    runs again without the embedding (_NoEmbedding), so a question that names a file or a function still gets its answer
    and nobody gets an error, or a manager an incident, for a search that was only half down. `degraded` is True either way.
    A failure of the second run (the database) propagates."""
    try:
        found = crew.retrieve(deps, project_id, question, k=k, purpose="answer", steps=steps, on_step=on_step)
        return found, bool(getattr(found, "degraded", False))
    except Exception as e:  # noqa: BLE001 - retried below without the embedder; a second failure propagates
        log.warning("search failed; retrying without the embedding", extra={"error": type(e).__name__})
        keyword = dataclasses.replace(deps, index=_NoEmbedding(deps.index))
        return crew.retrieve(keyword, project_id, question, k=k, purpose="answer", steps=steps, on_step=on_step), True


def _project_paths(deps, project_id: str) -> list[str] | None:
    """The project's indexed files (for a question that names one), or None when the index cannot list them."""
    try:
        return list(deps.index.all_paths(project_id))
    except Exception:  # noqa: BLE001 - the gate then takes any mention of the file
        return None


def search_quietly(index, project_id: str, question: str, k: int = 8) -> list[Hit]:
    """index.search, or nothing when the search is down: who a question goes to never depends on the embedder."""
    try:
        return index.search(project_id, question, k=k)
    except Exception as e:  # noqa: BLE001 - a person is still reached without the search
        log.warning("search failed while routing to a person", extra={"error": type(e).__name__})
        return []


AI_OFF_WHY = "The AI is off on this server: the answer quotes the sources, sentence by sentence."


def ai_answer(ctx: Ctx, project_id: str, question: str, hits=None) -> dict:
    """tier 1's attempt: answer_from with the lifecycle context's Deps."""
    return answer_from(ctx.deps, project_id, question, hits)


def answer_from(deps, project_id: str, question: str, hits=None) -> dict:
    """{"result": QAResult | None, "hits", "draft", "cited_paths", "why", "check", "retrieval", "writer"}, plus "crew" (the
    research crew's summary: counts only) when the crew ran here. `hits`: the crew's evidence when the caller gathered it
    already (the Guide's first steps). `writer`: "model" or "extractive" for an answer that passed both gates, else None.

    One writer and two gates for every deterministic answer (the Guide's tier 1 without an agent loop, and chat.question):
    the model drafts from the context blocks (with no model on the server, AIUnavailable, the offline writer quotes
    them: "extractive", and the answer says so); the retrieval gate (answer_gate) on what the draft cites; the citation
    check on every sentence, with at most one rewrite by the model; then each sentence's [n] pointed at a source that
    supports it (align_markers)."""
    extra, notes = {}, []
    if hits is None:
        found, degraded = gather(deps, project_id, question)
        hits, extra = found.hits, {"crew": {**found.summary(), "degraded": degraded}}
        notes = [SEARCH_DOWN] if degraded else []
    none = {"result": None, "hits": hits, "draft": "", "cited_paths": [], "check": None, "retrieval": None, "writer": None, **extra}
    if not hits:
        return {**none, "why": ["Not enough evidence: nothing in the code, documents or KT calls matches the question."] + notes}
    context = [{"n": i + 1, "label": label_for(h.chunk), "text": h.chunk.content[:2500]} for i, h in enumerate(hits)]
    prompt = f"Answer the question in the data from its context blocks.\n\n{embed_data({'question': question, 'context': context})}"
    llm = deps.llm
    try:  # the question is untrusted: it reaches the model inside the data block only
        draft = llm.structured(prompt, QADraft, system=prompts.QA)
    except AIUnavailable as e:
        from app.agents.core.registry import RulesMode

        if isinstance(e, RulesMode):  # an admin's choice for this organisation: the Guide writes nothing
            return {**none, "why": ["Not enough evidence: the Guide is set to rules, so it writes no answers."] + notes}
        llm, notes = OfflineLLM(), notes + [AI_OFF_WHY]  # no model on this server: the sources can still be quoted
        draft = llm.structured(prompt, QADraft)
    except LLMError:  # the model failed, or wrote something outside its schema: nothing of it is used
        return {**none, "why": ["Not enough evidence: the assistant is unavailable right now."] + notes}
    valid = list(dict.fromkeys(n for n in draft.cited if 1 <= n <= len(hits)))
    cited_hits = [hits[n - 1] for n in valid]
    cited = [h.chunk for h in cited_hits]
    out = {**none, "draft": draft.answer if valid else "", "cited_paths": _code_paths(cited)}
    if not (draft.sufficient and valid):
        return {**out, "why": ["Not enough evidence: the sources found do not contain the answer."] + notes}
    paths = _project_paths(deps, project_id) if any(_is_file(n) for n in given_names(question)) else None
    gate = answer_gate(question, cited_hits, paths=paths)
    out["retrieval"] = gate
    if not gate["ok"]:
        return {**out, "why": [gate_refusal(gate)] + notes}
    check = citation_check(draft.answer, cited)
    repaired = False
    if not check["ok"] and not isinstance(llm, OfflineLLM):  # one repair pass: the model rewrites what the check could not find
        unsupported = [r["text"] for r in check["sentences"] if r["claim"] and not r["sources"]]
        try:
            fixed = llm.structured(
                "Rewrite the answer in the data so that every sentence is stated by the cited context blocks. Correct or "
                "remove the sentences listed as unsupported; keep file, function and command names exactly as the blocks "
                "write them; add no new facts; keep the same block numbers in `cited`.\n\n"
                f"{embed_data({'question': question, 'answer': draft.answer, 'unsupported': unsupported, 'context': context})}",
                QADraft, system=prompts.QA)
        except (AIUnavailable, LLMError):
            fixed = None
        if fixed is not None and fixed.sufficient:
            valid2 = list(dict.fromkeys(n for n in fixed.cited if 1 <= n <= len(hits)))
            cited2 = [hits[n - 1].chunk for n in valid2]
            check2 = citation_check(fixed.answer, cited2) if valid2 else {"ok": False, "sentences": []}
            gate2 = answer_gate(question, [hits[n - 1] for n in valid2], paths=paths) if valid2 else {"ok": False}
            if check2["ok"] and gate2["ok"]:
                draft, cited, check, repaired, valid, gate = fixed, cited2, check2, True, valid2, gate2
                cited_hits = [hits[n - 1] for n in valid2]
                out = {**out, "draft": fixed.answer, "cited_paths": _code_paths(cited2), "retrieval": gate2}
    if not check["ok"]:
        loose = next((r["text"] for r in check["sentences"] if r["claim"] and not r["sources"]), "the answer")
        return {**out, "check": check,
                "why": [f"Not enough evidence: \"{loose[:100]}\" is not supported by any one cited source."] + notes}
    # each claim's [n] at a source that supports it; then the [n] (context block numbers) as citation numbers ([1] first)
    writer = writer_of(llm)
    text = renumber(align_markers(draft.answer, cited, valid), valid)
    result = QAResult(status="answered", answer=text, citations=[qa._citation(c) for c in cited],
                      kt_clips=qa.kt_clips_for(deps, project_id, [c for c in cited if c.source_type == "code"]),
                      confidence=draft.confidence, writer=writer, crew=extra.get("crew"))
    how = GATE_HOW[gate["rule"]].format(sim=gate["similarity"], min=MIN_SIMILARITY, names=", ".join(gate["names"][:3]))
    return {**out, "result": result, "check": check, "writer": writer,
            "why": [f"Answered from {len(cited)} source{'s' if len(cited) != 1 else ''}: {how}.",
                    "Every sentence is supported by the cited sources (its names, numbers and negation agree)."
                    + (" The first draft was rewritten once to drop what the sources did not say." if repaired else "")] + notes}


# ---------------------------------------------------------------- tier 2
def paraphrase(question: str) -> str:
    """Another person's question without their words: its subject, as a short list of terms."""
    words = sorted(terms(question))[:6]
    return f"A similar question about {', '.join(words)}" if words else "A similar question"


def related(ctx: Ctx, project_id: str, question: str) -> list[dict]:
    """Verified answers and answered expert questions like this one, best first, without who asked or answered, and without
    another hire's words: an expert answer's question is shown as a paraphrase."""
    cands = [("verified_answer", a.id, a.question, a.answer, a.question) for a in ctx.lc.verified_answers(project_id)]
    cands += [("expert_answer", str(r["id"]), r["question"], r["answer"], paraphrase(r["question"]))
              for r in ctx.lc.answered_requests(project_id) if r.get("answer")]
    out, seen = [], set()
    for kind, rid, q, a, shown in sorted(cands, key=lambda c: -similarity(question, c[2])):
        sim = similarity(question, q)
        if sim < RELATED_SIMILARITY or len(out) >= MAX_RELATED:
            break
        if norm_question(q) in seen:
            continue
        seen.add(norm_question(q))
        out.append({"kind": kind, "id": rid, "question": shown, "answer": a, "similarity": round(sim, 2)})
    return out


def _chunk_id(item: dict, answers=()) -> str:
    """The index chunk an item is cited by. A verified answer that was saved from an expert's answer lives in that answer's own
    chunk ('verified-<request id>', api/lifecycle.py verify_answer), so a question is one source, not two."""
    if item["kind"] != "verified_answer":
        return f"verified-{item['id']}"
    saved = next((a for a in answers if a.id == item["id"]), None)
    return f"verified-{saved.source_request_id}" if saved and saved.source_request_id else f"va-{item['id']}"


def count_reuse(ctx: Ctx, org_id: str, project_id: str, chunk_ids: list[str], user_id: str) -> int:
    """A verified answer (ours: 'va-<id>', or an expert's answer: 'verified-<request id>') shown again saves a person's time."""
    n = 0
    for cid in chunk_ids:
        if cid.startswith("va-"):
            ctx.lc.bump_reuse(cid[3:])
        elif cid.startswith("verified-"):  # an expert's answer that was saved as a verified answer keeps its chunk (api/lifecycle.py)
            for a in ctx.lc.verified_answers(project_id):
                if a.source_request_id == cid[len("verified-"):]:
                    ctx.lc.bump_reuse(a.id)
        if cid.startswith(("va-", "verified-")):
            ctx.lc.record_metric(org_id, project_id, "answer.reused", user_id, {"chunk_id": cid}, ctx.clock.now())
            n += 1
    return n


# ---------------------------------------------------------------- tiers 3 and 4
def domain_owner(ctx: Ctx, project_id: str, paths: list[str], asker_id: str) -> tuple[User | None, str]:
    """The owner of `paths` (the code the answer cited, else the best-ranked code found)."""
    store, project = ctx.store, ctx.store.get_project(project_id)
    rules = parse_codeowners(store.codeowners(project_id))

    def eligible(u: User) -> bool:  # a deactivated member gets no new questions
        return u.id != asker_id and u.role in PERSON_ROLES and u.status == "active" and store.is_member(project_id, u.id)

    for path in paths:
        rule = rule_for(rules, path)
        if rule and rule[1] and rule[0] not in CATCH_ALL:
            people = [u for u in store.users_by_github(project.org_id, rule[1]) if eligible(u)]
            if people:
                return people[0], f"CODEOWNERS gives {rule[0]} to {people[0].name}, and the answer needs {path}."
    speakers = Speakers(store, store.members(project_id))
    started = {m.id: m.started_at or datetime.min.replace(tzinfo=timezone.utc) for m in store.list_meetings(project_id)}
    for link in sorted((l for l in store.kt_links(project_id) if l.file_path in paths),
                       key=lambda l: started.get(l.meeting_id) or datetime.min.replace(tzinfo=timezone.utc), reverse=True):
        _, who = speakers.for_link(link)
        if who and eligible(who):
            return who, f"{who.name} explained {link.file_path} most recently ({link.meeting_title or 'a KT call'})."
    for path in paths:  # the catch-all owner of the code, before the managers
        rule = rule_for(rules, path)
        if rule and rule[1]:
            people = [u for u in store.users_by_github(project.org_id, rule[1]) if eligible(u)]
            if people:
                return people[0], f"{people[0].name} owns {path} (the catch-all CODEOWNERS rule)."
    managers = [m for m in store.managers(project_id) if m.id != asker_id and m.status == "active"]
    if managers:
        return managers[0], f"{managers[0].name} manages the project."
    return None, "There is nobody else on this project to ask."


def _today_start(ctx: Ctx) -> datetime:
    return ctx.clock.now().replace(hour=0, minute=0, second=0, microsecond=0)  # today in UTC, the time metrics are kept in


def interruptions_today(ctx: Ctx, org_id: str, user_id: str) -> int:
    return sum(m.subject_id == user_id for m in ctx.lc.metrics(org_id, ["interruption"], _today_start(ctx)))


def urgent_today(ctx: Ctx, org_id: str, asker_id: str) -> int:
    return sum(m.subject_id == asker_id for m in ctx.lc.metrics(org_id, ["route.urgent"], _today_start(ctx)))


def _hm(value: str | None, default: tuple[int, int]) -> tuple[int, int]:
    try:
        h, m = str(value).split(":", 1)
        return int(h), int(m)
    except (AttributeError, ValueError):
        return default


def in_office_hours(hours: dict | None, now: datetime) -> bool:
    """Office hours {days: ["Tue", "Thu"], start: "15:00", end: "16:00"} (UTC); none set: always."""
    if not hours:
        return True
    days = {str(d)[:3].lower() for d in hours.get("days") or []}
    if days and DAYS[now.weekday()] not in days:
        return False
    start, end = _hm(hours.get("start"), (0, 0)), _hm(hours.get("end"), (24, 0))
    return start <= (now.hour, now.minute) < end


def deliver(ctx: Ctx, project_id: str, question: str, target: User, asker: User, message_id: str | None, tier: int, urgent: bool,
            draft: str, why: list[str], cited_paths: list[str] = (), suggested: dict | None = None) -> dict:
    """The one path a question takes to a person (routing's tiers 3 and 4, and the chat's hand-off): the expert request (with
    the AI draft), and a notification now or a place in their digest, by the rules in the module docstring."""
    org, now = asker.org_id, ctx.clock.now()
    rid = ctx.store.create_expert_request(project_id, question, target.id, asker.id, message_id, draft=(draft or None) and draft[:4000])
    prefs = ctx.lc.expert_preferences(target.id)
    used = interruptions_today(ctx, org, target.id)
    urgent_now = urgent and tier == 3 and urgent_today(ctx, org, asker.id) < URGENT_PER_DAY
    hours = in_office_hours(prefs.office_hours, now)
    now_ok = urgent_now or (hours and used < prefs.max_interruptions_per_day)
    why = list(why)
    if urgent and not urgent_now:
        why.append("Urgent reaches a mentor at once, once a day: this one follows the usual rules.")
    if now_ok:
        link = f"/experts?message={message_id}&project={project_id}" if message_id else f"/experts?project={project_id}"
        ctx.store.notify(target.id, "expert_request", f"{asker.name} asked{' (urgent)' if urgent_now else ''}: {question[:200]}", link)
        ctx.lc.record_metric(org, project_id, "interruption", target.id, {"request_id": rid, "urgent": urgent_now}, now)
        if urgent_now:
            ctx.lc.record_metric(org, project_id, "route.urgent", asker.id, {"request_id": rid}, now)
        why.append("Marked urgent: their mentor was told now." if urgent_now else
                   f"They were told now ({used + 1} of {prefs.max_interruptions_per_day} today).")
    elif not hours:
        why.append(f"Outside {target.name}'s office hours: it waits in their digest.")
    else:
        why.append(f"{target.name} has had {used} interruptions today: it waits in their digest.")
    ctx.lc.record_metric(org, project_id, "route.escalated", asker.id,
                         {"tier": tier, "request_id": rid, "target": target.id, "draft": (draft or "")[:4000],
                          "delivery": "now" if now_ok else "digest", "urgent": urgent_now, "cited_paths": list(cited_paths)[:10],
                          **({"suggested": suggested} if suggested else {})}, now)
    return {"tier": tier, "status": "routed", "message_id": message_id, "request_id": rid,
            "routed_to": {"id": target.id, "name": target.name, "role": target.role}, "delivery": "now" if now_ok else "digest",
            "escalation": {"available": tier == 3, "next_tier": 4 if tier == 3 else None}, "why": why}


def hand_to(ctx: Ctx, project_id: str, question: str, target: User, message_id: str | None, tier: int, urgent: bool, draft: str,
            why: list[str], cited_paths: list[str] = (), suggested: dict | None = None) -> dict:
    return deliver(ctx, project_id, question, target, ctx.user, message_id, tier, urgent, draft, why, cited_paths, suggested)


def suggested_target(ctx: Ctx, project_id: str, route: dict | None, asker_id: str) -> tuple[User, str] | None:
    """(the person the agentic Guide suggested, why), if the code finds them eligible for a question: an active expert,
    manager or admin on this project, not the asker. The Guide may only suggest someone its tools named."""
    if not route or not route.get("to_user_id"):
        return None
    u = ctx.store.get_user(str(route["to_user_id"]))
    if not u or u.id == asker_id or u.role not in PERSON_ROLES or u.status != "active" or u.org_id != ctx.user.org_id \
            or not ctx.store.is_member(project_id, u.id):
        return None
    because = str(route.get("why") or "").strip()[:300]
    return u, f"The Guide suggests {u.name}" + (f": {because}" if because else ".")


# ---------------------------------------------------------------- the router
def _offered(ctx: Ctx, user_id: str, message_id: str | None) -> dict | None:
    """The attempt the Guide made on an offered turn (its draft and cited paths), if this user asked it."""
    if not message_id:
        return None
    return next((m.props for m in reversed(ctx.lc.metrics(ctx.user.org_id, ["route.offered"]))
                 if m.subject_id == user_id and m.props.get("message_id") == message_id), None)


def attempt(ctx: Ctx, project_id: str, question: str, ask_person: bool = False, offered_message_id: str | None = None) -> dict:
    """Tier 1's attempt (the model answers from retrieved sources, through both gates), or, when the asker asked for a person,
    the retrieval alone plus the draft and cited paths of the turn the Guide offered a person on."""
    if not ask_person:
        return {**ai_answer(ctx, project_id, question), "earlier": None}
    earlier = _offered(ctx, ctx.user.id, offered_message_id)
    hits = search_quietly(ctx.index, project_id, question)  # the person is reached even when the search is down
    return {"result": None, "hits": hits, "check": None, "draft": (earlier or {}).get("draft", ""), "earlier": earlier,
            "route": (earlier or {}).get("route"),
            "cited_paths": (earlier or {}).get("cited_paths") or _code_paths([h.chunk for h in hits if h.chunk.source_type == "code"][:1]),
            "why": ["You asked for a person."]}


def _module(ai: dict) -> str | None:
    top = next(iter(ai["cited_paths"]), None) or next((h.chunk.ref["path"] for h in ai["hits"] if h.chunk.source_type == "code"), None)
    return module_of(top) if top else None


def tier1(ctx: Ctx, project_id: str, question: str, ai: dict) -> dict | None:
    """The answer, when the attempt passed both gates."""
    if not ai["result"]:
        return None
    store, lc, user = ctx.store, ctx.lc, ctx.user
    # who wrote it and how the evidence was found stay with the chat turn (the Ask page's label after a reload)
    result: QAResult = ai["result"].model_copy(update={"writer": ai.get("writer"), "crew": ai.get("crew")})
    mid = store.save_chat(project_id, user.id, question, result)
    reused = count_reuse(ctx, user.org_id, project_id, [c.chunk_id for c in result.citations if c.chunk_id], user.id)
    lc.record_metric(user.org_id, project_id, "route.asked", user.id, {"tier": 1, "module": _module(ai), "message_id": mid}, ctx.clock.now())
    answer = result.model_dump(include={"answer", "citations", "kt_clips", "confidence"})
    return {"tier": 1, "status": "answered", "message_id": mid, **answer, "check": ai["check"], "reused": reused,
            "retrieval": ai.get("retrieval"), "escalation": {"available": True, "next_tier": 3}, "why": list(ai["why"])}


def tier2(ctx: Ctx, project_id: str, question: str, ai: dict, ask_person: bool = False) -> dict | None:
    """A verified answer to a similar question, when there is one (and the asker did not ask for a person)."""
    found = related(ctx, project_id, question) if not ask_person else []
    if not ask_person:  # verified answers the agentic Guide found and judged to answer it (each passed RELATED_SIMILARITY)
        known = {norm_question(f["question"]) for f in found}
        for item in ai.get("related") or []:
            if len(found) < MAX_RELATED and norm_question(item["question"]) not in known:
                known.add(norm_question(item["question"]))
                found.append(item)
    if not found:
        return None
    store, lc, user = ctx.store, ctx.lc, ctx.user
    best, answers = found[0], lc.verified_answers(project_id)
    result = QAResult(status="answered", answer=best["answer"], confidence=best["similarity"],
                      citations=[Citation(chunk_id=_chunk_id(best, answers), source_type="verified_answer",
                                          label=f"Verified answer: {best['question'][:60]}", ref={"question": best["question"]})])
    mid = store.save_chat(project_id, user.id, question, result)
    count_reuse(ctx, user.org_id, project_id, [_chunk_id(f, answers) for f in found], user.id)
    lc.record_metric(user.org_id, project_id, "route.asked", user.id, {"tier": 2, "module": _module(ai), "message_id": mid}, ctx.clock.now())
    return {"tier": 2, "status": "related", "message_id": mid, "related": found, "escalation": {"available": True, "next_tier": 3},
            "why": list(ai["why"]) + [f"A verified answer to a similar question (similarity {best['similarity']:.2f}, "
                                      f"at least {RELATED_SIMILARITY})."]}


def to_person(ctx: Ctx, project_id: str, question: str, ai: dict, ask_person: bool = False, urgent: bool = False,
              offered_message_id: str | None = None) -> dict:
    """Nothing answered it: offer a person (and send nothing), or, when the asker asked, hand it to their mentor (tier 3) or
    the domain owner of the code it cited (tier 4)."""
    store, lc, user = ctx.store, ctx.lc, ctx.user
    org, module, why = user.org_id, _module(ai), list(ai["why"])
    mentor = _mentor_of(ctx, project_id, user.id)
    suggested = suggested_target(ctx, project_id, ai.get("route"), user.id)
    if not ask_person:
        offer = QAResult(status="not_enough_information", answer=f"{qa.NOT_ENOUGH} You can ask a person.")
        mid = store.save_chat(project_id, user.id, question, offer)
        lc.record_metric(org, project_id, "route.asked", user.id, {"tier": 1, "module": module, "message_id": mid}, ctx.clock.now())
        lc.record_metric(org, project_id, "route.offered", user.id,
                         {"message_id": mid, "draft": (ai["draft"] or "")[:4000], "cited_paths": ai["cited_paths"][:10],
                          **({"route": ai["route"]} if suggested else {})}, ctx.clock.now())
        who = (f"your mentor, {mentor.name}" if mentor else f"{suggested[0].name}, whom the Guide suggests" if suggested
               else "the person who owns this code")
        return {"tier": 1, "status": "offered", "message_id": mid, "check": ai["check"],
                "escalation": {"available": True, "next_tier": 3 if mentor else 4},
                "why": why + [f"Nothing was sent yet: ask a person and it goes to {who}."]}
    tier, target = (3, mentor) if mentor else (4, None)
    if target is None:
        target, reason = suggested if suggested else domain_owner(ctx, project_id, ai["cited_paths"], user.id)
        why.append("You have no active mentor on this project: it goes to the domain owner. " + reason)
    else:
        why.append(f"{mentor.name} is your mentor.")
    if ai.get("earlier"):
        mid = offered_message_id
    else:
        routed = QAResult(status="not_enough_information", answer=f"{qa.NOT_ENOUGH} It went to {target.name}." if target else qa.NOT_ENOUGH,
                          routed_to=target.id if target else None)
        mid = store.save_chat(project_id, user.id, question, routed)
        lc.record_metric(org, project_id, "route.asked", user.id, {"tier": tier, "module": module, "message_id": mid}, ctx.clock.now())
    if not target:
        return {"tier": 4, "status": "unrouted", "message_id": mid, "escalation": {"available": False, "next_tier": None}, "why": why}
    if ai["draft"]:
        why.append("The AI draft is attached.")
    return hand_to(ctx, project_id, question, target, mid, tier, urgent, ai["draft"], why, ai["cited_paths"],
                   ai.get("route") if suggested else None)


def route(ctx: Ctx, project_id: str, question: str, ask_person: bool = False, urgent: bool = False,
          offered_message_id: str | None = None) -> dict:
    """The tiers in order (the Guide's subgraph runs these same steps as its nodes: app/agents/specialists/guide/agent.py)."""
    ai = attempt(ctx, project_id, question, ask_person, offered_message_id)
    return (tier1(ctx, project_id, question, ai) or tier2(ctx, project_id, question, ai, ask_person)
            or to_person(ctx, project_id, question, ai, ask_person, urgent, offered_message_id))


def escalate(ctx: Ctx, request: dict, urgent: bool = False) -> dict:
    """Tier 4: the question the mentor has (or had) goes to the domain owner of the code it cited, with the AI draft."""
    project_id = str(request["project_id"])
    earlier = next((m for m in reversed(ctx.lc.metrics(ctx.user.org_id, ["route.escalated"]))
                    if m.props.get("request_id") == str(request["id"])), None)
    paths = (earlier.props.get("cited_paths") if earlier else None) or \
        _code_paths([h.chunk for h in search_quietly(ctx.index, project_id, request["question"]) if h.chunk.source_type == "code"][:1])
    suggested = suggested_target(ctx, project_id, earlier.props.get("suggested") if earlier else None, ctx.user.id)
    if suggested and suggested[0].id == str(request["expert_id"]):
        suggested = None  # the mentor already has it: the domain owner by the rules
    target, reason = suggested or domain_owner(ctx, project_id, paths, ctx.user.id)
    if not target or target.id == str(request["expert_id"]):
        return {"tier": 4, "status": "unrouted", "message_id": request.get("message_id"),
                "escalation": {"available": False, "next_tier": None},
                "why": [reason if not target else f"{target.name}, the domain owner, already has this question."]}
    draft = request.get("draft") or (earlier.props.get("draft") if earlier else "") or ""
    return hand_to(ctx, project_id, request["question"], target, str(request["message_id"]) if request.get("message_id") else None, 4,
                   urgent, draft, ["Escalated past the mentor.", reason] + (["The AI draft is attached."] if draft else []), paths)


def digests(ctx: Ctx, org_id: str) -> int:
    """The daily digest (sent by the tick): one notification to each person with `digest` on whose questions wait for them in
    it, once per question. Returns how many people were told."""
    waiting: dict[str, list[str]] = {}
    sent = {m.props.get("request_id") for m in ctx.lc.metrics(org_id, ["route.digested"])}
    for m in ctx.lc.metrics(org_id, ["route.escalated"]):
        rid = m.props.get("request_id")
        if m.props.get("delivery") == "digest" and rid not in sent:
            r = ctx.store.get_expert_request(rid)
            if r and r.get("status") == "open":
                waiting.setdefault(m.props["target"], []).append(rid)
    told = 0
    for person, ids in waiting.items():
        if not ctx.lc.expert_preferences(person).digest:
            continue
        r = ctx.store.get_expert_request(ids[0])
        ctx.store.notify(person, "digest", f"{len(ids)} question{'s' if len(ids) != 1 else ''} wait for you in today's digest.",
                         f"/experts?project={r['project_id']}")
        who = ctx.store.get_user(person)
        if who:  # the same digest by email when SMTP is set up: the count and a link, never the questions (services/mailing.py)
            from app.services import mailing

            mailing.expert_digest(ctx, who, len(ids), str(r["project_id"]))
        for rid in ids:
            ctx.lc.record_metric(org_id, str(r["project_id"]), "route.digested", person, {"request_id": rid}, ctx.clock.now())
        told += 1
    return told


def _mentor_of(ctx: Ctx, project_id: str, user_id: str) -> User | None:
    for ob in ctx.store.onboardings(project_id):
        if ob.new_hire_id == user_id:
            p = ctx.lc.get_program(ob.id)
            if p and p.mentor_id and p.mentor_id != user_id:
                mentor = ctx.store.get_user(p.mentor_id)
                return mentor if mentor and mentor.status == "active" else None  # deactivated: as if there were none
    return None
