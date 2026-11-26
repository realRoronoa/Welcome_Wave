"""Call every configured model once: fast, smart, fallback, embeddings, and (optionally) video input.

    MODE=gcp GCP_PROJECT=... MODEL_FAST=... MODEL_SMART=... python -m scripts.check_models [gs://bucket/sample.mp4]

Run it after changing a model ID and before a demo: preview models can be withdrawn or limited to the global
endpoint, and this fails in seconds instead of in front of an audience.
"""
from __future__ import annotations

import sys
import time

from pydantic import BaseModel

from app.agents.llm import GeminiLLM, MediaRef
from app.config import get_settings
from app.services.embeddings import VertexEmbedder


class Ping(BaseModel):
    answer: str


def main() -> int:
    s = get_settings()
    models = (("fast", s.model_fast), ("smart", s.model_smart), ("fallback", (s.model_fallback or s.model_fast).split(",")[0].strip()))
    s.llm_retries = 1
    s.model_fallback = ""  # test each model on its own, never silently through the fallback
    llm, failed = GeminiLLM(s), False
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
    if len(sys.argv) > 1:
        s.model_fast = models[0][1]
        try:
            out = llm.structured("Describe the first frame in five words.", Ping, media=[MediaRef(uri=sys.argv[1], start_s=0, end_s=10)])
            print(f"ok    video     {s.model_fast:32s} -> {out.answer[:40]!r}")
        except Exception as e:  # noqa: BLE001
            failed = True
            print(f"FAIL  video     {s.model_fast:32s} {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
