"""Runtime settings. Every value can be set with an environment variable of the same name (upper-case).

Outside demo mode they are also read from `.env` in the working directory (environment variables win). Demo mode reads no
env file: a developer's `backend/.env` holds real credentials for self-hosted work, and the demo must behave the same on
every machine, with no keys. Something meant for the demo (a GEMINI_API_KEY to try real answers, a Calendly signing key to
test the webhook) is passed in the command's own environment, on purpose."""
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings: PydanticBaseSettingsSource, env_settings: PydanticBaseSettingsSource,
                                   dotenv_settings: PydanticBaseSettingsSource, file_secret_settings: PydanticBaseSettingsSource):
        """The usual order (arguments, then the environment, then `.env`, then secret files), except that `.env` is skipped
        when the mode is demo: given as an argument, else in the environment, else in `.env` itself, else by default."""
        def dotenv_unless_demo() -> dict:
            found = dotenv_settings()
            mode = getattr(init_settings, "init_kwargs", {}).get("mode") or env_settings().get("mode") or found.get("mode") or "demo"
            return {} if mode == "demo" else found

        return init_settings, env_settings, dotenv_unless_demo, file_secret_settings

    # demo        = everything in memory, offline LLM, local sample repo, made-up accounts (no keys needed)
    # self_hosted = the real application on one machine: Postgres (data kept), Firebase sign-in, Gemini (API key), files on
    #               disk, background work in the API process, GitHub repositories connected by URL and watched for changes
    # gcp         = Cloud SQL, Vertex AI / Gemini, GCS, Pub/Sub, GitHub App
    mode: str = "demo"
    env: str = "dev"  # dev | prod. prod refuses to start in demo mode or with development secrets.

    database_url: str = "postgresql+psycopg://ww:ww@localhost:5432/welcomewave"
    db_pool_size: int = 5
    db_max_overflow: int = 5
    checkpoint_pool_size: int = 4

    # Google Cloud
    gcp_project: str = ""
    gcp_region: str = "asia-south1"
    vertex_location: str = "global"
    embedding_location: str = ""  # defaults to gcp_region; set if the embedding model is served elsewhere
    gcs_bucket: str = ""
    pubsub_prefix: str = "ww"

    # Models. Confirm the exact generally-available IDs in Vertex AI Model Garden before the demo
    # (python -m scripts.check_models calls each one once).
    gemini_api_key: str = ""  # optional: AI Studio key for local dev instead of Vertex AI
    model_fast: str = "gemini-3.1-flash"
    model_smart: str = "gemini-3.1-pro"
    model_fallback: str = "gemini-3.1-flash"
    embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = 768
    embedding_batch: int = 64
    embedding_concurrency: int = 8
    # Embedding requests per minute, paced on our side. 0 = automatic: 90 with a Gemini API key (the AI Studio free tier
    # allows 100 a minute), no limit on Vertex AI. A 429 from Google is waited out either way (services/embeddings.py).
    embedding_rpm: int = 0
    # LangSmith tracing sends every agent run's prompts and outputs (code excerpts, redacted transcripts, new hires'
    # questions) to LangSmith, a third party: an organisation turns it on explicitly (docs/SECURITY.md, docs/DEPLOY.md).
    # Off unless LANGSMITH_TRACING=true; no other LangChain or LangSmith variable can turn it on (app/container.py).
    langsmith_tracing: bool = False
    llm_timeout_s: int = 90
    llm_video_timeout_s: int = 900
    llm_retries: int = 2

    # Identity Platform (Firebase Auth). The web API key is public by design; it only identifies the project.
    firebase_api_key: str = ""
    firebase_auth_domain: str = ""
    firebase_project_id: str = ""  # defaults to gcp_project; self-hosted mode needs only a (free) Firebase project
    # Development only: sign in against the Firebase Auth emulator (e.g. 127.0.0.1:9099), which issues unsigned tokens.
    # Refused when ENV=prod.
    firebase_auth_emulator_host: str = ""
    signup_enabled: bool = True
    require_verified_email: bool = True

    # Web
    public_url: str = ""  # https://app.example.com - used for links in notifications and the GitHub callback
    cors_origins: str = ""  # comma-separated; empty = same origin only (the web app proxies /api)
    chat_rate_per_minute: int = 12
    max_upload_mb: int = 25
    # Cloud Scheduler sends the event outbox via POST /internal/outbox/flush with a Google-signed OIDC token
    outbox_flush_audience: str = ""  # the API's URL, the token's audience; empty turns the endpoint off
    outbox_flush_invoker: str = ""  # the scheduler service account's email; no other caller is accepted

    # GitHub App (read-only scopes: contents, metadata, pull requests, issues, checks)
    github_app_id: str = ""
    github_app_slug: str = ""  # the app's URL name, for the install link
    github_private_key: str = ""
    github_webhook_secret: str = "dev-secret"
    github_client_id: str = ""  # OAuth during installation: proves the installer can see the installation
    github_client_secret: str = ""

    # Scheduling (docs/specs/scheduling.md): KT sessions go on Google Calendar with a Google Meet link, or are booked through
    # a person's Calendly link; their recordings come back from Google Drive or are uploaded.
    google_oauth_client_id: str = ""  # an OAuth client (Web application) in the Google Cloud console; empty turns Google off
    google_oauth_client_secret: str = ""
    google_oauth_redirect_uri: str = ""  # default: PUBLIC_URL + /api/integrations/google/callback
    calendly_webhook_signing_key: str = ""  # the signing key of the Calendly webhook subscription; empty turns the webhook off
    # Encrypts the stored Google refresh tokens (any long random string). Self-hosted: generated into DATA_DIR when empty.
    # Required on Google Cloud when Google Calendar is set up (instances share no disk).
    token_encryption_key: str = ""
    recording_poll_s: int = 300  # self-hosted: how often ended sessions are checked for their Drive recording (0 = never)
    tick_every_s: int = 3600  # self-hosted: how often the daily tick is offered to each organisation (it runs once a day; 0 = never)

    # Agent behaviour
    qa_min_confidence: float = 0.5
    qa_min_similarity: float = 0.15
    meeting_segment_s: int = 600
    plan_horizon_days: int = 30
    max_plan_tasks: int = 12
    agent_retries: int = 3
    retry_sleep_s: float = 1.0
    event_lease_s: int = 300  # a claimed event whose handler stopped heartbeating for this long can be retried

    # Self-hosted mode
    data_dir: str = ".data"  # uploaded documents and call recordings (relative to the backend folder unless absolute)
    blob_signing_key: str = ""  # signs the browser's links to call recordings; generated into DATA_DIR when empty
    max_recording_mb: int = 2048  # an uploaded KT call recording
    github_token: str = ""  # optional read-only token: private repositories, and 5,000 GitHub API calls an hour instead of 60
    repo_poll_s: int = 120  # how often connected repositories are checked for new commits and pull requests (0 = never)
    local_workers: int = 3  # background jobs (indexing, call notes, reviews, plans) that run at once

    # Email (optional). With SMTP_HOST and a sender set, Welcome Wave emails invitations, pre-boarding links (when a manager asks),
    # the expert digest and the weekly summary. Port 587 uses STARTTLS, 465 an SSL connection from the start. Without them nothing
    # is sent and the web app shows the link instead (GET /api/config says `email: false`). docs/INTEGRATIONS.md.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""  # the From address; defaults to SMTP_USER

    demo_repo_path: str = "sample_repo"
    # MODE=demo only: the one password every demo account signs in with (POST /api/demo/login). It is shown on the demo
    # sign-in page, so it is a gate for the form, not a secret; production signs in through Identity Platform instead.
    demo_password: str = "welcome-wave"

    @field_validator("google_oauth_client_secret", "calendly_webhook_signing_key", "token_encryption_key", mode="before")
    @classmethod
    def _unused_is_unset(cls, value):
        """DEPLOY.md has 'unused' entered for a secret not in use yet: that turns the feature off, never into a guessable key."""
        return "" if isinstance(value, str) and value.strip().lower() == "unused" else value

    @property
    def is_demo(self) -> bool:
        return self.mode == "demo"

    @property
    def is_self_hosted(self) -> bool:
        return self.mode == "self_hosted"

    @property
    def firebase_project(self) -> str:
        return self.firebase_project_id or self.gcp_project

    @property
    def data_path(self) -> str:
        from pathlib import Path

        p = Path(self.data_dir)
        return str(p if p.is_absolute() else Path(__file__).resolve().parents[1] / p)

    @property
    def ai_ready(self) -> bool:
        """A model answers: a Gemini API key, Vertex AI on Google Cloud, or the demo's offline stand-in. Without one, self-hosted
        mode still runs, and says the AI is off where it is needed."""
        return bool(self.gemini_api_key) or self.mode in {"gcp", "demo"}

    @property
    def smtp_sender(self) -> str:
        return self.smtp_from or self.smtp_user

    @property
    def email_ready(self) -> bool:
        """Email can be sent: an SMTP server and a From address are configured."""
        return bool(self.smtp_host and self.smtp_sender)

    @property
    def google_calendar_ready(self) -> bool:
        """Google Calendar can be connected: an OAuth client is configured. Each organisation then connects its own account."""
        return bool(self.google_oauth_client_id and self.google_oauth_client_secret)

    def production_problems(self, serves_http: bool = True) -> list[str]:
        """Settings that would make a production deployment unsafe. Checked at startup (workers do not serve webhooks)."""
        problems = []
        if self.mode not in {"demo", "self_hosted", "gcp"}:
            problems.append(f"MODE must be demo, self_hosted or gcp, not {self.mode!r}.")
        if self.env == "prod" and self.is_demo:
            problems.append("MODE=demo is not allowed when ENV=prod (demo mode trusts an X-Demo-User header).")
        if self.env == "prod" and self.firebase_auth_emulator_host:
            problems.append("FIREBASE_AUTH_EMULATOR_HOST is not allowed when ENV=prod (the emulator's tokens are not signed).")
        if self.is_self_hosted:
            if not self.firebase_project:
                problems.append("FIREBASE_PROJECT_ID is required in MODE=self_hosted (people sign in with Firebase).")
            if not self.firebase_api_key and not self.firebase_auth_emulator_host:
                problems.append("FIREBASE_API_KEY is required in MODE=self_hosted (the Firebase project's web API key).")
            if not self.database_url:
                problems.append("DATABASE_URL is required in MODE=self_hosted.")
        elif not self.is_demo:
            if serves_http and self.github_webhook_secret in {"", "dev-secret"}:
                problems.append("GITHUB_WEBHOOK_SECRET must be set to a real secret.")
            if serves_http and not (self.outbox_flush_audience and self.outbox_flush_invoker):
                problems.append("OUTBOX_FLUSH_AUDIENCE and OUTBOX_FLUSH_INVOKER are required: without the scheduled flush, an event "
                                "whose first send failed waits for the next GitHub webhook to be retried.")
            for name in ("gcp_project", "gcs_bucket", "database_url", "model_fast", "model_smart", "embedding_model"):
                if not getattr(self, name):
                    problems.append(f"{name.upper()} is required in MODE=gcp.")
            if self.google_calendar_ready and not self.token_encryption_key:
                problems.append("TOKEN_ENCRYPTION_KEY is required in MODE=gcp when Google Calendar is set up (it encrypts the stored "
                                "refresh tokens, and the instances share no disk to keep a generated key on).")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()


def dotenv_unless_demo(dotenv_settings: PydanticBaseSettingsSource):
    """For the other settings classes that read `.env` (the rate limiter, the audit log): the same rule as Settings, so a
    developer's `.env` changes the demo in no way, not even its limits or its retention. The mode is the one Settings finds."""
    def source() -> dict:
        found = dotenv_settings()
        return {} if Settings().is_demo else found

    return source
