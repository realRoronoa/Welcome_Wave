"""The layout of app/agents (app/agents/README.md): one package per roster agent, and imports that point one way.

    supervisor -> specialists -> core      supervisor, specialists -> tools -> core

Imports inside functions count too: a lazy import is still a dependency."""
from __future__ import annotations

import ast
import re
from pathlib import Path

from app.agents.core.registry import AGENTS
from app.agents.supervisor.graph import AGENT_NODES

AGENTS_DIR = Path(__file__).resolve().parents[1] / "app" / "agents"
SPECIALISTS = AGENTS_DIR / "specialists"


def _modules() -> list[Path]:
    return sorted(p for p in AGENTS_DIR.rglob("*.py") if "__pycache__" not in p.parts)


def _imports(path: Path) -> list[str]:
    """Every app.agents module `path` imports, at the top or inside a function (`from a.b import c` counts as a.b and a.b.c)."""
    out = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"{path}: use absolute imports (from app.agents...)"
            out += [node.module] + [f"{node.module}.{a.name}" for a in node.names]
        elif isinstance(node, ast.Import):
            out += [a.name for a in node.names]
    return [m for m in out if m and m.startswith("app.agents.")]


def _layer(module: str) -> str:
    """core, tools, supervisor, or specialists.<key>."""
    parts = module.split(".")[2:]
    return f"specialists.{parts[1]}" if parts[0] == "specialists" and len(parts) > 1 else parts[0]


def _own_layer(path: Path) -> str:
    rel = path.relative_to(AGENTS_DIR).parts
    if rel[0] == "specialists" and len(rel) > 2:
        return f"specialists.{rel[1]}"
    return rel[0].removesuffix(".py")


ALLOWED = {
    "core": {"core"},
    "tools": {"core", "tools"},
    "supervisor": {"core", "tools", "supervisor", "specialists"},
}


def test_every_roster_agent_is_a_specialist_package_with_an_agent_module():
    keys = {k for k in AGENTS if k != "supervisor"}
    packages = {p.name for p in SPECIALISTS.iterdir() if p.is_dir() and p.name != "__pycache__"}
    assert packages == keys == set(AGENT_NODES)
    for key in keys:
        assert (SPECIALISTS / key / "agent.py").is_file(), key
        init = (SPECIALISTS / key / "__init__.py").read_text(encoding="utf-8")
        assert ast.get_docstring(ast.parse(init)), f"specialists/{key}/__init__.py needs its one-line docstring"
    assert not [p for p in AGENTS_DIR.glob("*.py") if p.name != "__init__.py"], "modules belong in core/, supervisor/, specialists/ or tools/"


def test_imports_point_one_way():
    wrong = []
    for path in _modules():
        me = _own_layer(path)
        for module in _imports(path):
            to = _layer(module)
            if me.startswith("specialists."):
                ok = to in {"core", "tools", me}
            else:
                ok = to.split(".")[0] in ALLOWED[me]
            if not ok:
                wrong.append(f"{path.relative_to(AGENTS_DIR)} imports {module}")
    assert not wrong, "app/agents/README.md, 'The dependency rule':\n" + "\n".join(wrong)


def test_the_layout_rule_catches_a_specialist_importing_another(tmp_path):
    """The check above is only as good as its parsing: a planted cross-import, lazy and inside a function, is found."""
    planted = tmp_path / "agent.py"
    planted.write_text("def f():\n    from app.agents.specialists.coach.agent import tick\n    return tick\n")
    assert [_layer(m) for m in _imports(planted)] == ["specialists.coach", "specialists.coach"]
    assert _layer("app.agents.core.llm") == "core" and _layer("app.agents.supervisor.graph") == "supervisor"


def test_the_readme_names_every_module():
    readme = (AGENTS_DIR / "README.md").read_text(encoding="utf-8")
    tree = readme.split("```", 2)[1]  # the layout block
    missing = []
    for path in _modules():
        if path.name == "__init__.py":
            continue
        rel = path.relative_to(AGENTS_DIR)
        # a file by its name; a specialist's files also need its folder (the tree shows `coach/agent.py`)
        named = re.search(rf"(^|[\s/]){re.escape(path.name)}\b", tree, re.M)
        folder = f"{rel.parts[1]}/" if rel.parts[0] == "specialists" else f"{rel.parts[0]}/" if len(rel.parts) > 1 else ""
        if not named or folder not in tree:
            missing.append(str(rel))
    assert not missing, f"add to the layout in app/agents/README.md: {missing}"


DOCS = Path(__file__).resolve().parents[2] / "docs"
POINTER = re.compile(r"`` `(?P<code>[^`]+)` ``\s+\(\[(?P<name>[\w.]+):(?P<n>\d+)\]\((?P<path>[^)#\s]+)#L(?P<line>\d+)\)\)")
LINE_LINK = re.compile(r"\[[\w.]+:\d+\]\([^)]+#L\d+\)")


def test_langgraph_md_line_pointers_say_what_the_page_claims():
    """docs/LANGGRAPH.md writes each pointer as `code` ([file:line](path#Lline)): that line must still hold that code. When
    lines move, the message says where the code is now."""
    doc = DOCS / "LANGGRAPH.md"
    text = doc.read_text(encoding="utf-8")
    pointers = list(POINTER.finditer(text))
    assert len(pointers) >= 40 and len(pointers) == len(LINE_LINK.findall(text)), "every line link is a checked pointer"
    wrong = []
    for m in pointers:
        target = (doc.parent / m["path"]).resolve()
        assert target.name == m["name"] and m["n"] == m["line"], m[0]
        lines = target.read_text(encoding="utf-8").splitlines()
        n = int(m["line"])
        if n > len(lines) or m["code"] not in lines[n - 1]:
            now = [i + 1 for i, line in enumerate(lines) if m["code"] in line]
            wrong.append(f"{m['name']}:{n} no longer holds {m['code']!r} (now at {now or 'nowhere'})")
    assert not wrong, "update docs/LANGGRAPH.md:\n" + "\n".join(wrong)
