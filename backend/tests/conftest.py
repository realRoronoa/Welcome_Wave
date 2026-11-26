import os
from datetime import date
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.adapters.memory import InlineBus, LocalBlobStorage, LocalRepoSource, MemoryIndex, MemoryStore
from app.agents.deps import Deps
from app.agents.llm import FakeLLM
from app.agents.supervisor import build_supervisor
from app.config import Settings, get_settings
from app.ports import Meeting, Onboarding, Project, Repository, User
from app.services.embeddings import HashEmbedder
from app.services.redaction import RegexRedactor

SAMPLE = Path(__file__).resolve().parents[1] / "sample_repo"

# Rate limits are off in tests: many tests act as one person, or one address, many times a minute. A test that checks the
# limiter turns it on itself (tests/test_ratelimit.py). Set here, before any test module imports app.main, which builds an app.
os.environ["RATE_LIMIT_ENABLED"] = "false"


@pytest.fixture(scope="session", autouse=True)
def scratch_dirs(tmp_path_factory):
    """Tests never write into backend/. The demo container keeps uploads in LocalBlobStorage(".blobs"), relative to the working
    directory, and self-hosted mode keeps files in DATA_DIR (".data" by default): both go to this run's temporary folder."""
    root = tmp_path_factory.mktemp("runtime")
    mp = pytest.MonkeyPatch()
    mp.setenv("DATA_DIR", str(root / "data"))
    original = LocalBlobStorage.__init__

    def init(self, path: str = ".blobs"):
        original(self, path if os.path.isabs(path) else str(root / path))

    mp.setattr(LocalBlobStorage, "__init__", init)
    get_settings.cache_clear()  # app.main read the settings at import, before DATA_DIR was set
    yield root
    mp.undo()
    get_settings.cache_clear()

PR_FILES = {
    7: [
        {
            "filename": "engine/validate.py",
            "status": "modified",
            "patch": "@@ -26,6 +26,9 @@ def check_privileged(resource: dict, rule: Rule) -> list[str]:\n"
            "     if not rule.deny_privileged:\n"
            "         return []\n"
            "     containers = resource.get(\"spec\", {}).get(\"containers\", [])\n"
            "+    for c in containers:\n"
            "+        if c.get(\"securityContext\") is None:\n"
            "+            raise ValueError(\"missing securityContext\")\n"
            "     return [\n",
        }
    ]
}


@pytest.fixture
def settings():
    return Settings(mode="demo", agent_retries=1, retry_sleep_s=0)


@pytest.fixture
def world(settings, tmp_path):
    store = MemoryStore()
    store.add_project(Project(id="p1", org_id="o1", name="Mini Policy Engine", description="Admission policy engine"))
    store.add_user(User(id="u-mgr", org_id="o1", email="priya@x.dev", name="Priya", role="manager", github_login="priya-mgr"), "p1")
    store.add_user(User(id="u-hire", org_id="o1", email="vijay@x.dev", name="Vijay", role="new_hire", github_login="vijay"), "p1")
    store.add_user(User(id="u-ravi", org_id="o1", email="ravi@x.dev", name="Ravi", role="expert", github_login="ravi-dev"), "p1")
    store.add_onboarding(Onboarding(id="ob1", project_id="p1", new_hire_id="u-hire", manager_id="u-mgr", start_date=date.today()))
    store.add_repo(Repository(id="r1", project_id="p1", full_name="demo/mini-policy-engine"))
    store.add_meeting(Meeting(id="m1", project_id="p1", title="KT #1: validation engine", room="kt-1"))
    index = MemoryIndex(HashEmbedder())
    llm = FakeLLM()
    repos = LocalRepoSource(str(SAMPLE), issues=[{"number": 12, "title": "Validate privileged init containers", "labels": ["good first issue"]}], prs=PR_FILES)
    deps = Deps(settings=settings, llm=llm, index=index, store=store, repos=repos,
                blobs=LocalBlobStorage(str(tmp_path)), redactor=RegexRedactor(), bus=InlineBus())
    return deps


@pytest.fixture
def graph(world):
    return build_supervisor(world, MemorySaver())
