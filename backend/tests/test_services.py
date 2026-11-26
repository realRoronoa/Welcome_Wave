from app.agents.core.schemas import KTLink, ScreenMoment, Utterance
from app.adapters.memory import MemoryIndex
from app.services.chunking import chunk_code, module_of, split_identifiers
from app.services.codeowners import owners_for, parse_codeowners
from app.services.coverage import coverage_map, gaps
from app.services.diff import parse_patch
from app.services.embeddings import HashEmbedder
from app.services.kt_linker import link_screen_moments, match_path
from app.services.redaction import RegexRedactor

from tests.conftest import SAMPLE


def test_code_chunks_keep_symbols_and_line_numbers():
    text = (SAMPLE / "engine/validate.py").read_text()
    chunks = chunk_code("p1", "engine/validate.py", text, "abc")
    by_symbol = {c.symbol: c for c in chunks}
    assert {"ValidationError", "validate_resource", "check_labels", "check_privileged"} <= set(by_symbol)
    ref = by_symbol["validate_resource"].ref
    lines = text.splitlines()
    assert lines[ref["start_line"] - 1].startswith("def validate_resource")
    assert ref["path"] == "engine/validate.py" and ref["commit"] == "abc"


def test_identifiers_split_for_keyword_search():
    assert split_identifiers("validateResource check_labels") == "validate resource check labels"
    assert module_of("engine/validate.py") == "engine" and module_of("README.md") == "(root)"


def test_codeowners_last_match_wins():
    rules = parse_codeowners((SAMPLE / ".github/CODEOWNERS").read_text())
    assert owners_for(rules, "engine/validate.py") == ["ravi-dev"]
    assert owners_for(rules, "README.md") == ["priya-mgr"]


def test_patch_parsing_tracks_new_line_numbers():
    d = parse_patch("a.py", "@@ -1,2 +10,3 @@\n keep\n+added line\n-removed\n tail\n")
    assert d.added == {11: "added line"} and d.has_line(10) and d.has_line(12)
    assert d.contains("added   line")


def test_redaction_removes_contacts_and_keys():
    text, n = RegexRedactor().redact("mail ravi@corp.com or call +91 98765 43210, token ghp_" + "a" * 36)
    assert "@" not in text and "98765" not in text and "ghp_" not in text and n == 3


def _index_with_sample():
    idx = MemoryIndex(HashEmbedder())
    for path in ["engine/validate.py", "engine/policy.py", "webhooks/server.py", "engine/mutate.py"]:
        idx.upsert(chunk_code("p1", path, (SAMPLE / path).read_text()))
    return idx


def test_path_matching_handles_partial_screen_text():
    paths = ["engine/validate.py", "webhooks/server.py", "engine/policy.py"]
    assert match_path("./engine/validate.py:42", paths) == ("engine/validate.py", 0.97)
    assert match_path("server.py", paths)[0] == "webhooks/server.py"
    assert match_path("nothing.go", paths) is None


def test_kt_linker_links_path_symbol_and_meaning():
    idx = _index_with_sample()
    moments = [
        ScreenMoment(at_s=60, description="editor", visible_paths=["validate.py"], visible_symbols=["check_labels"]),
        ScreenMoment(at_s=200, description="function shown", visible_symbols=["handle_admission"]),
        ScreenMoment(at_s=400, description="code that adds default managed-by labels to a resource before validation"),
    ]
    speech = [Utterance(start_s=395, end_s=410, speaker="Ravi", text="this is where we apply defaults and set labels on metadata")]
    links = link_screen_moments("p1", "m1", "KT", moments, speech, idx, meeting_end_s=600)
    by_method = {l.method: l for l in links}
    assert by_method["path"].file_path == "engine/validate.py" and by_method["path"].symbol == "check_labels"
    assert by_method["path"].start_line and by_method["path"].end_s == 200
    assert by_method["symbol"].file_path == "webhooks/server.py"
    assert by_method["semantic"].file_path == "engine/mutate.py" and by_method["semantic"].confidence <= 0.75


def test_coverage_finds_modules_nobody_explained():
    paths = ["engine/validate.py", "engine/policy.py", "webhooks/server.py"]
    links = [KTLink(meeting_id="m1", file_path="engine/validate.py", start_s=0, end_s=120, confidence=0.9, method="path")]
    rows = coverage_map(paths, links)
    assert gaps(rows) == ["webhooks"]
    assert next(r for r in rows if r.module == "engine").kt_minutes == 2.0


def test_install_build_and_run_files_are_indexed_but_data_text_is_not():
    """A question like "what packages does the project need?" needs requirements.txt and pyproject.toml in the index."""
    from app.services.chunking import should_index

    for path in ["requirements.txt", "requirements-dev.txt", "backend/requirements.in", "pyproject.toml", "setup.cfg",
                 "Dockerfile", "deploy/Dockerfile.prod", "Makefile", "src/main.cpp", "app/Program.cs"]:
        assert should_index(path, 1000), path
    for path in ["data/plots.txt", "notes.txt", "node_modules/x/requirements.txt", "big.toml"]:
        assert not should_index(path, 1000 if path != "big.toml" else 500_000), path
