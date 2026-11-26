"""The Librarian (the ingestion agent): turns a repository and documents into the searchable index, incrementally on every push.

Limits. A repository is indexed only within INDEX_MAX_FILES files, INDEX_MAX_MB of text and INDEX_MAX_CHUNKS chunks (Settings).
They are checked before anything is written or embedded: past one, indexing stops with a TooLarge error that says which limit,
its value and its setting (the managers' incident notice and the run record show it), and the previous index stays as it was.
Data files (.json, .yaml, .yml) over INDEX_MAX_DATA_FILE_KB are not indexed, like lock files. An uploaded PDF over
MAX_PDF_PAGES pages, or a Word file that would unpack to more than MAX_DOCX_UNPACKED_MB, is not read (app/services/documents.py).

A full re-index keeps the old index until the new one is in place. Files whose chunks the new index reproduces are written
over in place; a file whose chunks moved has its old chunks removed just before its new ones are written; files that are gone
are removed last. A failure part-way (an embedding quota, a restart) leaves the old index of everything not yet reached."""
from __future__ import annotations

from app.agents.core.deps import Deps
from app.ports import Repository, SourceTooLarge, TooLarge
from app.services.chunking import chunk_code, chunk_document, should_index
from app.services.documents import extract_pages

CODEOWNERS_PATHS = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")
BATCH = 200
DATA_EXTS = (".json", ".yaml", ".yml")


def _upsert(deps: Deps, chunks) -> int:
    n = 0
    for i in range(0, len(chunks), BATCH):
        n += deps.index.upsert(chunks[i : i + BATCH])
    return n


def indexable(settings, path: str, size: int) -> bool:
    """should_index, and no data file (.json, .yaml, .yml) over INDEX_MAX_DATA_FILE_KB."""
    if not should_index(path, size):
        return False
    limit = getattr(settings, "index_max_data_file_kb", 0)
    return not (limit and path.lower().endswith(DATA_EXTS) and size > limit * 1024)


def _plan(deps: Deps, repo: Repository, files: dict[str, str], sha: str) -> dict[str, set[str]]:
    """The files to index and the ids of the chunks each will have, within the limits: TooLarge before anything is written."""
    s, pid = deps.settings, repo.project_id
    sizes = {p: len(t.encode()) for p, t in files.items()}
    paths = [p for p in sorted(files) if indexable(s, p, sizes[p])]
    nothing = " Nothing was changed: the previous index stays. Index a smaller repository, or raise the limit in the server's settings."
    if s.index_max_files and len(paths) > s.index_max_files:
        raise TooLarge(f"{repo.full_name} has {len(paths):,} files to index; the limit is {s.index_max_files:,} (INDEX_MAX_FILES).{nothing}")
    total = sum(sizes[p] for p in paths)
    if s.index_max_mb and total > s.index_max_mb * 1024 * 1024:
        raise TooLarge(f"{repo.full_name} has {total / 1048576:,.0f} MB of text to index; the limit is {s.index_max_mb:,} MB "
                       f"(INDEX_MAX_MB).{nothing}")
    plan: dict[str, set[str]] = {}
    count = 0
    for p in paths:
        plan[p] = {c.id for c in chunk_code(pid, p, files[p], sha)}
        count += len(plan[p])
        if s.index_max_chunks and count > s.index_max_chunks:
            raise TooLarge(f"{repo.full_name} has more than {s.index_max_chunks:,} code sections to index (INDEX_MAX_CHUNKS).{nothing}")
    return plan


def _indexed(deps: Deps, project_id: str) -> dict[str, set[str]]:
    """The chunk ids the index holds now, by file."""
    have = deps.index.code_chunks(project_id, content=False)
    out: dict[str, set[str]] = {}
    for c in have:
        out.setdefault(c.ref.get("path", ""), set()).add(c.id)
    if getattr(have, "truncated", False):  # past ports.CODE_CHUNK_CAP: the files it left out, one by one
        for path in deps.index.all_paths(project_id):
            if path not in out:
                out[path] = {c.id for c in deps.index.file_chunks(project_id, path)}
    return out


def _write(deps: Deps, project_id: str, files: dict[str, str], paths: list[str], sha: str, replace: bool) -> int:
    """Chunk and upsert the files, BATCH chunks at a time. `replace`: each file's old chunks are removed just before the
    batch that writes its first new ones, so it is missing from the index only while that batch is embedded."""
    n, batch, fresh = 0, [], []

    def flush() -> None:
        nonlocal n
        if replace and fresh:
            deps.index.delete_paths(project_id, list(fresh))
        if batch:
            n += deps.index.upsert(list(batch))
        batch.clear()
        fresh.clear()

    for path in paths:
        fresh.append(path)
        for chunk in chunk_code(project_id, path, files[path], sha):
            batch.append(chunk)
            if len(batch) >= BATCH:
                flush()
    flush()
    return n


def ingest_repo(deps: Deps, repo: Repository, ref: str, changed: list[str] = ()) -> dict:
    """Index the whole repository at the commit `ref` points to now, from `repo.default_branch`, which is recorded only
    once the index is built: a failed rebuild after a default-branch switch is tried again, not taken for a push.
    Ingestion of one repository (this and pushes) runs one at a time, so a full index and a push never interleave
    their deletes and inserts. The old index stays until the new one replaces it (the module docstring).

    Like a push, it flags what changed: answers citing a chunk that is gone or in a `changed` path (the paths of a
    push that switched the default branch), and KT links on those paths or on removed files."""
    pid, changed = repo.project_id, set(changed)
    with deps.store.ingest_lock(repo.id):
        sha = deps.repos.head_sha(repo.full_name, ref, repo.installation_id) or ref
        current = deps.store.get_repo(repo.id)
        if current.status == "ready" and current.last_indexed_sha == sha:  # e.g. two connects at once: the first did it
            if current.default_branch != repo.default_branch:  # a branch renamed on GitHub: same commit, new name
                deps.store.update_repo(repo.id, default_branch=repo.default_branch)
            return {"files": 0, "chunks": 0, "skipped": f"already indexed at {sha}"}
        deps.store.update_repo(repo.id, status="indexing")
        try:
            try:
                files = deps.repos.files(repo.full_name, sha, repo.installation_id)
            except SourceTooLarge as e:  # stopped while reading: the same limits, reported the same way
                raise TooLarge(f"{e} Nothing was changed: the previous index stays.") from None
            plan = _plan(deps, repo, files, sha)
            owners = next((files[p] for p in CODEOWNERS_PATHS if p in files), "")
            if owners:
                deps.store.set_codeowners(pid, owners)
            old = _indexed(deps, pid)
            touched = [p for p in sorted(old) if p in changed or p not in files]
            whole = set(touched) | (old.keys() - plan.keys())  # every old chunk of these goes; of the others, the ones not rebuilt
            gone = [cid for p in sorted(old) for cid in sorted(old[p] if p in whole else old[p] - plan[p])]
            moved = {p for p in plan if old.get(p, set()) - plan[p]}  # files whose old chunks the new ones do not replace
            n = _write(deps, pid, files, [p for p in plan if p not in moved], sha, replace=False)
            n += _write(deps, pid, files, [p for p in plan if p in moved], sha, replace=True)
            deps.index.delete_paths(pid, [p for p in sorted(old) if p not in plan])  # files that are gone, or no longer indexed
            stale_answers = deps.store.mark_answers_stale(gone)
            deps.store.mark_kt_code_changed(pid, touched)
        except Exception:
            deps.store.update_repo(repo.id, status="failed")  # offers Re-index; a retry sets it back to indexing
            raise
        deps.store.update_repo(repo.id, status="ready", last_indexed_sha=sha, default_branch=repo.default_branch)
    return {"files": len(files), "chunks": n, "answers_marked_stale": stale_answers}


def ingest_push(deps: Deps, repo: Repository, changed: list[str], removed: list[str], branch: str) -> dict:
    """Re-index the touched files as they are at the branch head, not at the push's own commit: pushes can be delivered
    and handled out of order. The head is resolved once, so every file and the chunks' commit label agree, and pushes to
    one repository run one at a time, so whatever the order the last one to run indexes the newest code.

    A push is not skipped when the head is already the indexed commit: a later push handled first re-indexed only its
    own paths at that head, and this push's paths still need it."""
    touched = list(dict.fromkeys(changed + removed))
    with deps.store.ingest_lock(repo.id):
        sha = deps.repos.head_sha(repo.full_name, branch, repo.installation_id) or branch
        texts = {path: deps.repos.file(repo.full_name, path, sha, repo.installation_id) for path in changed}
        gone = deps.index.delete_paths(repo.project_id, touched)  # after the fetches: the index is without them only briefly
        stale_answers = deps.store.mark_answers_stale(gone)
        deps.store.mark_kt_code_changed(repo.project_id, touched)
        chunks = []
        for path, text in texts.items():
            if text is not None and indexable(deps.settings, path, len(text.encode())):
                chunks += chunk_code(repo.project_id, path, text, sha)
            if path in CODEOWNERS_PATHS and text:
                deps.store.set_codeowners(repo.project_id, text)
        n = _upsert(deps, chunks)
        deps.store.update_repo(repo.id, last_indexed_sha=sha)  # the code view reads the same commit the index was built from
    return {"reindexed_files": len(changed), "removed_files": len(removed), "chunks": n, "answers_marked_stale": stale_answers}


def ingest_document(deps: Deps, project_id: str, document_id: str, uri: str, title: str, mime: str) -> dict:
    pages = extract_pages(deps.blobs.get(uri), mime)
    n = _upsert(deps, chunk_document(project_id, document_id, title, pages))
    return {"pages": len(pages), "chunks": n}
