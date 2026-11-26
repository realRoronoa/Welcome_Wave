"""Check every configured model: the model list first, then one call each (fast, smart, every fallback, embeddings, and
optionally video input).

    python -m scripts.check_models                       (self-hosted: GEMINI_API_KEY and MODEL_* from the environment or .env)
    MODE=gcp GCP_PROJECT=... MODEL_FAST=... MODEL_SMART=... python -m scripts.check_models [gs://bucket/sample.mp4]
    python -m scripts.check_models --list                list the models the key can use and look the configured ids up in
                                                         that list; no model is called

With a Gemini API key, the ids are first looked up in the list of models the key can use, so a misspelt or retired id is
named before anything is called. Run it after changing a model id and before a demo: preview models can be withdrawn or
limited to the global endpoint, and this fails in seconds instead of in front of an audience.
"""
from __future__ import annotations

import sys
import time

from pydantic import BaseModel

from app.config import get_settings


class Ping(BaseModel):
    answer: str


def configured(s) -> list[tuple[str, str]]:
    """(label, model id) for MODEL_FAST, MODEL_SMART and every model of the MODEL_FALLBACK chain, each id once."""
    out, seen = [], set()
    chain = [m.strip() for m in (s.model_fallback or "").split(",") if m.strip()]
    for label, model in [("fast", s.model_fast), ("smart", s.model_smart)] + [(f"fallback{i + 1}", m) for i, m in enumerate(chain)]:
        if model and model not in seen:
            seen.add(model)
            out.append((label, model))
    return out


def listed_models(s) -> set[str] | None:
    """The ids the Gemini API key can call generateContent with; None on Vertex AI (no key), where Model Garden is the list."""
    if not s.gemini_api_key:
        return None
    from google import genai

    names, client = set(), genai.Client(api_key=s.gemini_api_key)  # kept in a name: the pager needs the open client
    for m in client.models.list(config={"page_size": 1000}):
        if "generateContent" in (getattr(m, "supported_actions", None) or []):
            names.add((m.name or "").removeprefix("models/"))
    return names


def main(argv: list[str]) -> int:
    s = get_settings()
    models = configured(s)
    failed = False
    try:
        listed = listed_models(s)
    except Exception as e:  # noqa: BLE001
        print(f"FAIL  list      could not list the key's models: {e}")
        return 1
    if listed is not None:
        if "--list" in argv:
            print("\n".join(sorted(listed)))
        for label, model in models:
            known = model in listed
            failed |= not known
            print(f"{'ok   ' if known else 'FAIL '} listed    {label:9s} {model}" + ("" if known else "  (not a model this key can use)"))
        if failed or "--list" in argv:
            return 1 if failed else 0
    elif "--list" in argv:
        print("Listing needs GEMINI_API_KEY; on Vertex AI, see Model Garden.")
        return 1

    from app.agents.core.llm import GeminiLLM, MediaRef
    from app.services.embeddings import VertexEmbedder

    s.llm_retries = 1
    s.model_fallback = ""  # test each model on its own, never silently through the fallback
    llm = GeminiLLM(s)
    for label, model in models:
        s.model_fast = model
        started = time.perf_counter()
        try:
            out = llm.structured("Reply with the word ok.", Ping)
            print(f"ok    {label:9s} {model:32s} {time.perf_counter() - started:5.1f}s  -> {out.answer[:20]!r}")
        except Exception as e:  # noqa: BLE001
            failed = True
            print(f"FAIL  {label:9s} {model:32s} {e}")
    try:
        v = VertexEmbedder(s).embed(["validate resource against policy", "check labels"])
        print(f"ok    embedding {s.embedding_model:32s} dim={len(v[0])}")
        assert len(v[0]) == s.embedding_dim, "dimension does not match the vector(768) column"
    except Exception as e:  # noqa: BLE001
        failed = True
        print(f"FAIL  embedding {s.embedding_model:32s} {e}")
    video = [a for a in argv if not a.startswith("--")]
    if video:
        s.model_fast = models[0][1]
        try:
            out = llm.structured("Describe the first frame in five words.", Ping, media=[MediaRef(uri=video[0], start_s=0, end_s=10)])
            print(f"ok    video     {s.model_fast:32s} -> {out.answer[:40]!r}")
        except Exception as e:  # noqa: BLE001
            failed = True
            print(f"FAIL  video     {s.model_fast:32s} {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
