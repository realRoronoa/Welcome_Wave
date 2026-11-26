"""eval/rag_eval.py's unseen corpus (eval/README.md, "Unseen corpus"): the golden set loads with its own split, the clone's
commit is checked before anything is indexed, and the report groups one-word queries by style. The clone itself is not in
CI: the test that indexes it runs only where the clone is."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from eval import rag_eval as R


def _write(path: Path, cases: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(c) for c in cases) + "\n")
    return path


CASE = {"id": "u-1", "q": "Where is the router?", "category": "locate", "answerable": True,
        "expect": [{"kind": "code", "path": "starlette/routing.py", "text": "class Router:", "grade": 3}]}


def test_the_unseen_split_is_accepted_only_where_the_set_allows_it(tmp_path):
    unseen = _write(tmp_path / "u.jsonl", [{**CASE, "split": "unseen"}])
    assert [c["id"] for c in R.load_cases(unseen, R.SPLITS["unseen"])] == ["u-1"]
    with pytest.raises(SystemExit, match="split is not dev or holdout"):
        R.load_cases(unseen)  # the demo and self sets keep dev and holdout only
    dev = _write(tmp_path / "d.jsonl", [{**CASE, "split": "dev"}])
    with pytest.raises(SystemExit, match="split is not unseen"):
        R.load_cases(dev, R.SPLITS["unseen"])
    bad_style = _write(tmp_path / "s.jsonl", [{**CASE, "split": "unseen", "style": ""}])
    with pytest.raises(SystemExit, match="style"):
        R.load_cases(bad_style, R.SPLITS["unseen"])


def test_the_golden_unseen_set_has_the_shape_its_readme_states():
    cases = R.load_cases(R.EVAL / "golden_unseen.jsonl", R.SPLITS["unseen"])
    answerable = [c for c in cases if c["answerable"]]
    assert (len(cases), len(answerable)) == (70, 55)
    assert sum(bool(c.get("named")) for c in answerable) == 14
    assert sorted(c["q"] for c in cases if c.get("style") == "short") == ["opentelemetry tracing", "sessions", "trusted hosts", "url_for"]
    assert all(e["kind"] == "code" and e.get("text") and "start_line" not in e for c in answerable for e in c["expect"])


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t.invalid", "-c", "commit.gpgsign=false",
                    "-c", "core.hooksPath=/dev/null", *args], check=True, capture_output=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git to make a clone (Cloud Build's python:3.12-slim has none)")
def test_a_folder_that_is_not_the_clone_at_the_commit_stops_the_run(tmp_path):
    with pytest.raises(SystemExit, match="is not a clone of Starlette"):
        R.unseen_commit(tmp_path)
    (tmp_path / "starlette").mkdir()
    (tmp_path / "starlette" / "routing.py").write_text("class Router:\n    pass\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "not starlette")
    with pytest.raises(SystemExit, match=f"not {R.UNSEEN_COMMIT[:12]}"):
        R.unseen_commit(tmp_path)


def test_style_groups_appear_only_when_the_set_marks_a_style():
    row = {"answerable": True, "category": "paraphrase", "split": "unseen", "named": False, "hit1": 1.0}
    grouped = R.groups([{**row, "style": "short"}, {**row, "style": None}])
    assert len(grouped["style:short"]) == 1 and len(grouped["style:sentence"]) == 1
    assert "split:unseen" in grouped
    assert not any(k.startswith("style:") for k in R.groups([{**row, "style": None}]))


@pytest.mark.skipif(not (Path(R.UNSEEN_DIR) / ".git").exists(), reason="the Starlette clone is not on this machine")
def test_the_clone_indexes_as_the_readme_says_and_every_anchor_resolves(tmp_path):
    corpus = R.build_unseen(False, str(tmp_path), Path(R.UNSEEN_DIR))
    assert len(corpus.chunks) == 812
    assert "62 files" in corpus.info
    cases = R.load_cases(R.EVAL / "golden_unseen.jsonl", R.SPLITS["unseen"])
    assert R.check_golden(corpus, cases) == []
