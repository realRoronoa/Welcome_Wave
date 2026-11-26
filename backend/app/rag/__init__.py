"""The Guide's research crew: multi-agent retrieval in front of every answer (docs/specs/rag-crew.md).

    text.py     one tokenizer for the crew: identifier-aware words, a light stemmer, stop words, subject words, names
    analyze.py  the Planner's rules: intent, names, subject, parts, and up to 5 queries in the project's own words
    bm25.py     BM25 over token lists (the keyword channel and the Judge's pool scorer)
    fuse.py     weighted reciprocal-rank fusion
    rerank.py   the Judge's lexical cross-scorer, its weights, and the reasons it gives in our words
    crew.py     retrieve() (a LangGraph subgraph, with the model planner and judge when a model can think) and search()

Nothing here writes, and nothing here is new to the outside world: it reads the run's project through the Index port and,
when a model can think, asks it for queries and grades inside embed_data. Every guard downstream is unchanged.
"""
