"""Generate the ER diagram from the table list below (kept in step with db/migrations/001_init.sql)."""
TABLES = {
    "organizations": ("#edf0f5", ["id PK", "name"]),
    "users": ("#edf0f5", ["id PK", "org_id FK", "firebase_uid", "role", "github_login"]),
    "projects": ("#edf0f5", ["id PK", "org_id FK", "name"]),
    "onboardings": ("#edf0f5", ["id PK", "project_id FK", "new_hire_id FK", "manager_id FK", "start_date", "status"]),
    "repositories": ("#ffffff", ["id PK", "project_id FK", "full_name", "installation_id", "last_indexed_sha", "codeowners"]),
    "documents": ("#ffffff", ["id PK", "project_id FK", "gcs_uri", "mime_type"]),
    "chunks": ("#e3f2f0", ["id PK (hash)", "project_id FK", "source_type", "path, symbol", "ref jsonb", "tsv tsvector", "embedding vector(768)", "stale"]),
    "meetings": ("#ffffff", ["id PK", "project_id FK", "room", "provider", "join_url", "starts_at", "status", "recording_uri", "summary"]),
    "transcript_segments": ("#ffffff", ["id PK", "meeting_id FK", "start_s, end_s", "speaker, text"]),
    "screen_moments": ("#ffffff", ["id PK", "meeting_id FK", "at_s", "visible_paths[]", "visible_symbols[]"]),
    "action_items": ("#ffffff", ["id PK", "meeting_id FK", "text", "converted_task_id"]),
    "kt_links": ("#e3f2f0", ["id PK", "meeting_id FK", "chunk_id FK", "file_path, symbol", "start_line, end_line", "start_s, end_s", "confidence, method", "code_changed_since"]),
    "tasks": ("#ffffff", ["id PK", "onboarding_id FK", "kind, status, origin", "due_date", "github_issue_number", "pr_number, pr_url"]),
    "approvals": ("#fff4e0", ["id PK", "project_id FK", "kind", "thread_id (LangGraph)", "payload jsonb", "status, decided_by"]),
    "reviews": ("#fff4e0", ["id PK", "task_id FK", "head_sha", "recommendation", "findings jsonb", "questions, answers", "manager_decision"]),
    "chat_messages": ("#ffffff", ["id PK", "project_id FK", "user_id FK", "citations, kt_clips", "cited_chunk_ids[]", "stale"]),
    "expert_requests": ("#ffffff", ["id PK", "message_id FK", "expert_id FK", "answer", "verified_chunk_id FK"]),
    "agent_runs": ("#edf0f5", ["id PK", "agent, trigger", "status", "langsmith_run_id"]),
}
EDGES = [
    ("organizations", "users"), ("organizations", "projects"), ("projects", "onboardings"), ("users", "onboardings"),
    ("projects", "repositories"), ("projects", "documents"), ("projects", "chunks"), ("projects", "meetings"),
    ("meetings", "transcript_segments"), ("meetings", "screen_moments"), ("meetings", "action_items"),
    ("meetings", "kt_links"), ("chunks", "kt_links"), ("onboardings", "tasks"), ("tasks", "reviews"),
    ("projects", "approvals"), ("projects", "chat_messages"), ("chat_messages", "expert_requests"), ("chunks", "expert_requests"),
]


def node(name, color, cols):
    rows = "".join(f'<TR><TD ALIGN="LEFT" BALIGN="LEFT"><FONT POINT-SIZE="9.5">{c}</FONT></TD></TR>' for c in cols)
    return (f'  {name} [label=<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="3" COLOR="#9aa6c2" BGCOLOR="{color}">'
            f'<TR><TD BGCOLOR="#1d2742"><FONT COLOR="white"><B>{name}</B></FONT></TD></TR>{rows}</TABLE>>];')


out = ['digraph ER {', '  graph [rankdir=LR, nodesep=0.22, ranksep=0.55, pad=0.15, fontname="Carlito"];',
       '  node [shape=plaintext, fontname="Carlito", fontcolor="#1d2742"];',
       '  edge [color="#55607a", arrowsize=0.6, arrowhead=crow, arrowtail=none, dir=both];']
out += [node(n, c, cols) for n, (c, cols) in TABLES.items()]
out += [f"  {a} -> {b};" for a, b in EDGES]
out.append("}")
open("er.dot", "w").write("\n".join(out))
