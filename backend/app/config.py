"""Runtime settings. Every value can be set with an environment variable of the same name (upper-case).

Outside demo mode they are also read from `.env` in the working directory (environment variables win). Demo mode reads no
env file: a developer's `backend/.env` holds real credentials for self-hosted work, and the demo must behave the same on
every machine, with no keys. Something meant for the demo (a GEMINI_API_KEY to try real answers, a Calendly signing key to
test the webhook) is passed in the command's own environment, on purpose."""
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


# The development database (docker compose's db service, known credentials). With ENV=prod it counts as not set, so a
# deployment that forgot DATABASE_URL refuses to start instead of trying localhost with ww:ww.
DEFAULT_DATABASE_URL = "postgresql+psycopg://ww:ww@localhost:5432/welcomewave"


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

    database_url: str = DEFAULT_DATABASE_URL  # the development database; with ENV=prod it counts as unset
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

    # Models. The defaults are generally available Gemini API ids (checked against the API's model list on 2026-11-17).
    # `python -m scripts.check_models` lists the models the key can use and calls each configured one once; on Vertex AI
    # confirm them in Model Garden. MODEL_FALLBACK is a comma-separated chain, each a different model with its own quota.
    gemini_api_key: str = ""  # optional: AI Studio key for local dev instead of Vertex AI
    model_fast: str = "gemini-3.5-flash-lite"
    model_smart: str = "gemini-3.5-flash"
    model_fallback: str = "gemini-3.5-flash,gemini-3.1-flash-lite,gemini-2.5-flash"
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
    # Who may create an organisation (POST /api/signup). Unset: MODE=self_hosted is one organisation (the first sign-up
    # creates it; after that people join by invitation) and MODE=gcp lets any verified login create its own. true opens
    # sign-up to everyone, false closes it, in either mode.
    signup_enabled: bool | None = None
    require_verified_email: bool = True

    # Web
    public_url: str = ""  # https://app.example.com - used for links in notifications and the GitHub callback
    cors_origins: str = ""  # comma-separated; empty = same origin only (the web app proxies /api)
    chat_rate_per_minute: int = 12
    chat_concurrency: int = 8  # questions the Guide answers at once in this process; one more is told to retry (429)
    api_threads: int = 100  # threads for blocking request work (sign-in, database, model calls); anyio's default is 40
    max_upload_mb: int = 25
    max_json_kb: int = 1024  # a request body other than an upload or a webhook (app/ratelimit.py BodyLimitMiddleware)
    # Cloud Scheduler sends the event outbox via POST /internal/outbox/flush with a Google-signed OIDC token
    outbox_flush_audience: str = ""  # the API's URL, the token's audience; empty turns the endpoint off
    outbox_flush_invoker: str = ""  # the scheduler service account's email; no other caller is accepted

    # GitHub App (read-only scopes: contents, metadata, pull requests, issues, checks)
    github_app_id: str = ""
    github_app_slug: str = ""  # the app's URL name, for the install link
    github_private_key: str = ""
    # Signs GitHub's webhook deliveries (and the install link's state). The default is public and only the demo accepts it:
    # elsewhere POST /webhooks/github answers 404 until a real secret (16 characters or more) is set.
    github_webhook_secret: str = "dev-secret"
    github_client_id: str = ""  # OAuth during installation: proves the installer can see the installation
    github_client_secret: str = ""

    # Scheduling (docs/specs/scheduling.md): KT sessions go on Google Calendar with a Google Meet link, or are booked through
    # a person's Calendly link; their recordings come back from Google Drive or are uploaded.
    google_oauth_client_id: str = ""  # an OAuth client (Web application) in the Google Cloud console; empty turns Google off
    google_oauth_client_secret: str = ""
    google_oauth_redirect_uri: str = ""  # default: PUBLIC_URL + /api/integrations/google/callback
    calendly_webhook_signing_key: str = ""  # the signing key of the Calendly webhook subscription; empty turns the webhook off
    # Welcome Wave Voice. All credentials remain server-side; only short-lived RTC/RTM tokens reach the browser.
    agora_app_id: str = ""
    agora_app_certificate: str = ""
    agora_customer_id: str = ""
    agora_customer_secret: str = ""
    murf_api_key: str = ""
    murf_voice_id: str = "Matthew"
    murf_locale: str = "en-US"
    voice_tool_url: str = ""  # public HTTPS origin for Agora's server-to-server custom tools
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

    # The Guide's research crew (app/rag, docs/specs/rag-crew.md). The model stages run only when a model can think.
    rag_model_plan: bool = True  # the model adds up to 4 rewrites and a hypothetical passage, alongside the rules' searches
    rag_model_judge: bool = True  # the model grades the top candidates 0-3, and the grade ranks first
    rag_plan_timeout_s: float = 6.0  # past this the model's plan is not waited for: the rules' searches stand alone
    rag_judge_timeout_s: float = 8.0  # past this the grades are not waited for: the lexical ranking stands
    rag_judge_top: int = 12  # candidates the model grades (each shown as at most 600 characters)
    rag_k_code: int = 12  # code hits each query fetches before fusion (scaled by the question's intent)
    rag_k_doc: int = 6  # document hits each query fetches
    rag_k_meeting: int = 6  # KT call hits each query fetches
    rag_k_verified: int = 4  # verified answers each query fetches

    # Self-hosted mode
    data_dir: str = ".data"  # uploaded documents and call recordings (relative to the backend folder unless absolute)
    blob_signing_key: str = ""  # signs the browser's links to call recordings; generated into DATA_DIR when empty
    max_recording_mb: int = 2048  # an uploaded KT call recording
    github_token: str = ""  # optional read-only token: private repositories, and 5,000 GitHub API calls an hour instead of 60
    # The GitHub users or organisations (comma-separated) whose PRIVATE repositories may be connected through GITHUB_TOKEN.
    # Empty: the token only raises the API limit, and only public repositories can be connected.
    github_allowed_owners: str = ""
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

    # Legal details (app/legal.py, docs/specs/legal-consent.md): who operates this server, as the terms of service, the privacy
    # policy and the data-processing agreement name them. GET /api/config shows them under `legal`. None is needed to start:
    # one that is not set (or is a left-over placeholder) is shown as "the company that operates this service", never as a
    # made-up name. `./scripts/selfhost.sh check` lists the ones still missing.
    legal_operator_name: str = ""  # the company's registered legal name
    legal_operator_address: str = ""  # its registered address, on one line
    legal_contact_email: str = ""  # where legal notices and questions go
    legal_privacy_email: str = ""  # where privacy requests go (access, erasure, complaints)
    legal_governing_law: str = ""  # the law the terms are governed by, as a phrase ("the laws of ..."): decide with your lawyer
    legal_courts: str = ""  # the courts that hear disputes, as a phrase ("the courts of ..."): decide with your lawyer
    # false (the default): the web app asks each person to accept the current terms and privacy policy at sign-in, and the
    # API refuses nothing. true: the API also answers 403 `legal_required` on workspace routes until they have. Turn it on
    # only once the web app that shows the prompt is the one deployed, or people are locked out with nothing to click.
    legal_enforce: bool = False

    # ---- reliability and data limits (release round, track D) ----
    # Indexing limits for one repository. Past any of them indexing stops before anything is embedded, the previous index
    # stays, and the managers are told which limit and its setting. Data files (.json, .yaml, .yml) larger than
    # INDEX_MAX_DATA_FILE_KB are not indexed at all: fixtures and data dumps, like lock files.
    index_max_files: int = 20_000
    index_max_mb: int = 150  # the indexable text, in total
    index_max_chunks: int = 60_000
    index_max_data_file_kb: int = 100
    # Database sessions of the app (not the migrator): a statement, a wait for a lock, or a transaction left open past
    # these is cancelled, so one slow query or a migration's lock cannot hold the connection pool. 0 turns one off.
    db_connect_timeout_s: int = 10
    db_statement_timeout_ms: int = 30_000
    db_lock_timeout_ms: int = 5_000
    db_idle_in_transaction_timeout_ms: int = 60_000
    # A chat question's embedding: at most this long, past ingestion's pacing and Google's "slow down" answers. Past it the
    # question fails fast with "search is busy" instead of waiting minutes behind an index build.
    query_embedding_deadline_s: float = 8.0
    # ---- end of track D settings ----

    demo_repo_path: str = "sample_repo"
    # MODE=demo only: the one password every demo account signs in with (POST /api/demo/login). It is shown on the demo
    # sign-in page, so it is a gate for the form, not a secret; production signs in through Identity Platform instead.
    demo_password: str = "welcome-wave"

    @field_validator("google_oauth_client_secret", "calendly_webhook_signing_key", "token_encryption_key", "github_webhook_secret",
                     mode="before")
    @classmethod
    def _unused_is_unset(cls, value):
        """DEPLOY.md has 'unused' entered for a secret not in use yet: that turns the feature off, never into a guessable key."""
        return "" if isinstance(value, str) and value.strip().lower() == "unused" else value

    @field_validator("signup_enabled", mode="before")
    @classmethod
    def _blank_is_default(cls, value):
        """SIGNUP_ENABLED= (empty, as a deploy template may pass it) means the mode's default, not an error."""
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("legal_enforce", mode="before")
    @classmethod
    def _blank_is_off(cls, value):
        """LEGAL_ENFORCE= (empty, as a deploy template may pass it) means off, not a server that refuses to start."""
        return False if isinstance(value, str) and not value.strip() else value

    @property
    def is_demo(self) -> bool:
        return self.mode == "demo"

    @property
    def signup_first_only(self) -> bool:
        """Sign-up creates the first organisation only (self-hosted, SIGNUP_ENABLED unset); the routes check whether one exists."""
        return self.signup_enabled is None and self.is_self_hosted

    @property
    def signup_open(self) -> bool:
        """Anyone with a verified login may create an organisation, whatever already exists."""
        return self.signup_enabled is True or (self.signup_enabled is None and not self.is_self_hosted)

    @property
    def allowed_github_owners(self) -> frozenset[str]:
        return frozenset(o.strip().lower() for o in self.github_allowed_owners.split(",") if o.strip())

    @property
    def github_app_configured(self) -> bool:
        return bool(self.github_app_id or self.github_app_slug)

    @property
    def github_secret_ready(self) -> bool:
        """The webhook secret may be used as a key: the demo's public one in the demo only, a real one anywhere else."""
        return self.is_demo or self.github_webhook_secret.strip() not in WEAK_SECRETS

    @property
    def github_webhooks_ready(self) -> bool:
        """POST /webhooks/github is served: a real secret, and on one machine only with a GitHub App (repositories connected
        by URL are polled, app/selfhost.py RepoWatcher, so they need no webhook)."""
        return self.github_secret_ready and (self.is_demo or not self.is_self_hosted or self.github_app_configured)

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

    @property
    def voice_ready(self) -> bool:
        return all((self.agora_app_id, self.agora_app_certificate, self.agora_customer_id,
                    self.agora_customer_secret, self.murf_api_key, self.voice_tool_url.startswith("https://")))

    def production_problems(self, serves_http: bool = True) -> list[str]:
        """Settings that would make a production deployment unsafe. Checked at startup (workers do not serve webhooks)."""
        problems = []
        if self.mode not in {"demo", "self_hosted", "gcp"}:
            problems.append(f"MODE must be demo, self_hosted or gcp, not {self.mode!r}.")
        if self.env not in {"dev", "prod"}:
            problems.append(f"ENV must be dev or prod, not {self.env!r}.")
        if self.env == "prod" and self.is_demo:
            problems.append("MODE=demo is not allowed when ENV=prod (demo mode trusts an X-Demo-User header). Set MODE explicitly.")
        if self.env == "prod" and self.firebase_auth_emulator_host:
            problems.append("FIREBASE_AUTH_EMULATOR_HOST is not allowed when ENV=prod (the emulator's tokens are not signed).")
        if self.env == "prod" and "*" in {o.strip() for o in self.cors_origins.split(",")}:
            problems.append("CORS_ORIGINS=* is not allowed when ENV=prod: name the web app's origin, or leave it empty (same origin).")
        if self.env == "prod" and not self.is_demo and serves_http:
            problems += self._public_url_problems()
        if serves_http and not self.is_demo and self.github_app_configured and not self._strong_webhook_secret():
            problems.append("GITHUB_WEBHOOK_SECRET must be a real secret of 16 characters or more when a GitHub App is set up "
                            "(GITHUB_APP_ID or GITHUB_APP_SLUG): the default and 'unused' are public.")
        if self.is_self_hosted:
            if not self.firebase_project:
                problems.append("FIREBASE_PROJECT_ID is required in MODE=self_hosted (people sign in with Firebase).")
            if not self.firebase_api_key and not self.firebase_auth_emulator_host:
                problems.append("FIREBASE_API_KEY is required in MODE=self_hosted (the Firebase project's web API key).")
            if not self._database_url_set:
                problems.append("DATABASE_URL is required in MODE=self_hosted (with ENV=prod the development default does not count).")
            if self.signup_enabled is True and self.github_token and self.allowed_github_owners:
                problems.append("SIGNUP_ENABLED=true with GITHUB_TOKEN and GITHUB_ALLOWED_OWNERS would let every organisation anyone "
                                "creates connect those owners' private repositories. Keep sign-up closed (leave SIGNUP_ENABLED unset: "
                                "people join by invitation), or empty GITHUB_ALLOWED_OWNERS.")
        elif not self.is_demo:
            if serves_http and not (self.outbox_flush_audience and self.outbox_flush_invoker):
                problems.append("OUTBOX_FLUSH_AUDIENCE and OUTBOX_FLUSH_INVOKER are required: without the scheduled flush, an event "
                                "whose first send failed waits for the next GitHub webhook to be retried.")
            for name in ("gcp_project", "gcs_bucket", "model_fast", "model_smart", "embedding_model"):
                if not getattr(self, name):
                    problems.append(f"{name.upper()} is required in MODE=gcp.")
            if not self._database_url_set:
                problems.append("DATABASE_URL is required in MODE=gcp (with ENV=prod the development default does not count).")
            if self.env == "prod" and serves_http and not self.firebase_api_key:
                problems.append("FIREBASE_API_KEY is required in MODE=gcp when ENV=prod (people sign in with Identity Platform; without it "
                                "every sign-in fails while the health checks pass): terraform output -raw firebase_api_key.")
            if self.google_calendar_ready and not self.token_encryption_key:
                problems.append("TOKEN_ENCRYPTION_KEY is required in MODE=gcp when Google Calendar is set up (it encrypts the stored "
                                "refresh tokens, and the instances share no disk to keep a generated key on).")
        return problems

    @property
    def _database_url_set(self) -> bool:
        return bool(self.database_url) and not (self.env == "prod" and self.database_url == DEFAULT_DATABASE_URL)

    def _strong_webhook_secret(self) -> bool:
        secret = self.github_webhook_secret.strip()
        return secret not in WEAK_SECRETS and len(secret) >= 16

    def _public_url_problems(self) -> list[str]:
        """ENV=prod: links in emails and the OAuth redirects need the server's real address, served over HTTPS (sign-in tokens
        travel with every request). Plain http is accepted only for this machine itself (localhost)."""
        from urllib.parse import urlsplit

        url = urlsplit(self.public_url.strip())
        if not self.public_url.strip() or url.scheme not in {"http", "https"} or not url.hostname:
            return ["PUBLIC_URL is required when ENV=prod: the address people open the web app at, e.g. https://onboarding.example.com."]
        if url.scheme == "http" and not (self.is_self_hosted and url.hostname in {"localhost", "127.0.0.1", "::1"}):
            return [f"PUBLIC_URL must use https:// when ENV=prod (it is {self.public_url.strip()!r}): sign-in tokens would cross the "
                    "network unencrypted."]
        return []


# Webhook secrets anyone can read: the code's default and the placeholders in the env examples.
WEAK_SECRETS = frozenset({"", "dev-secret", "change-me", "changeme", "secret"})


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
