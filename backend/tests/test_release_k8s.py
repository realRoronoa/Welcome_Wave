"""Containers and Kubernetes: the Kustomize overlays in infra/k8s render to what docs/KUBERNETES.md promises, the settings and
Secrets they set are ones the application really reads, the scheduled calls match Terraform's, the migration runs before
any new code, and the images and docker-compose.yml keep the containers unprivileged.

Rendering uses `kubectl kustomize`, which reads files only and never talks to a cluster; the tests that need it are skipped
where kubectl is not installed (GitHub's runners have it). Nothing here starts a container or writes into backend/."""
from __future__ import annotations

import http.server
import importlib.util
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

from app.api.audit import AuditSettings
from app.config import Settings
from app.ratelimit import RateLimitSettings

ROOT = Path(__file__).resolve().parents[2]
K8S = ROOT / "infra/k8s"
OVERLAYS = ("staging", "production", "self-hosted")
GKE = ("staging", "production")
KUBECTL = shutil.which("kubectl") or ("/usr/local/bin/kubectl" if Path("/usr/local/bin/kubectl").exists() else None)
needs_kubectl = pytest.mark.skipif(KUBECTL is None, reason="kubectl is not installed (kubectl kustomize renders the overlays)")
POD_KINDS = {"Deployment", "Job", "CronJob"}
APP_IMAGES = {"welcome-wave-api", "welcome-wave-web"}


def render(path: Path) -> list[dict]:
    out = subprocess.run([KUBECTL, "kustomize", str(path)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return [d for d in yaml.safe_load_all(out.stdout) if d]


@pytest.fixture(scope="module")
def rendered() -> dict[str, list[dict]]:
    if KUBECTL is None:
        pytest.skip("kubectl is not installed")
    return {name: render(K8S / "overlays" / name) for name in OVERLAYS}


def find(docs, kind, name=None):
    found = [d for d in docs if d["kind"] == kind and (name is None or d["metadata"]["name"] == name)]
    return found


def one(docs, kind, name):
    [doc] = find(docs, kind, name)
    return doc


def pod_spec(doc: dict) -> dict:
    if doc["kind"] == "CronJob":
        return doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    return doc["spec"]["template"]["spec"]


def config(docs) -> dict[str, str]:
    [cm] = [d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"].startswith("ww-config-")]
    return cm["data"]


def host_of(url: str) -> str:
    return urlsplit(url).hostname


# ---------------------------------------------------------------- what every overlay renders
@needs_kubectl
@pytest.mark.parametrize("overlay", OVERLAYS)
def test_overlay_renders_into_one_restricted_namespace_with_no_secret_values(rendered, overlay):
    docs = rendered[overlay]
    ns = one(docs, "Namespace", "welcome-wave")
    assert ns["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "restricted"
    for d in docs:
        if d["kind"] != "Namespace":
            assert d["metadata"]["namespace"] == "welcome-wave", d["metadata"]["name"]
        assert d["metadata"]["labels"]["app.kubernetes.io/part-of"] == "welcome-wave", d["metadata"]["name"]
    # Secrets are created outside git (by hand or by the External Secrets Operator), never rendered from these files
    assert not find(docs, "Secret")
    policy = one(docs, "NetworkPolicy", "default-deny")
    assert policy["spec"]["podSelector"] == {} and set(policy["spec"]["policyTypes"]) == {"Ingress", "Egress"}


@needs_kubectl
@pytest.mark.parametrize("overlay", OVERLAYS)
def test_every_pod_runs_unprivileged_with_limits(rendered, overlay):
    for d in rendered[overlay]:
        if d["kind"] not in POD_KINDS:
            continue
        spec, name = pod_spec(d), d["metadata"]["name"]
        sc = spec["securityContext"]
        assert sc["runAsNonRoot"] is True and isinstance(sc["runAsUser"], int) and sc["runAsUser"] > 0, name
        assert sc["seccompProfile"] == {"type": "RuntimeDefault"}, name
        assert spec["automountServiceAccountToken"] is False and spec["enableServiceLinks"] is False, name
        for c in spec["containers"] + spec.get("initContainers", []):
            csc = c["securityContext"]
            assert csc["allowPrivilegeEscalation"] is False, (name, c["name"])
            assert csc["readOnlyRootFilesystem"] is True, (name, c["name"])
            assert csc["capabilities"] == {"drop": ["ALL"]}, (name, c["name"])
            assert {"cpu", "memory"} <= set(c["resources"]["requests"]) and "memory" in c["resources"]["limits"], (name, c["name"])
            # the app's images are set at release time (deploy.sh); anything else is pinned by digest
            assert c["image"] in APP_IMAGES or re.search(r"@sha256:[0-9a-f]{64}$", c["image"]), c["image"]


@needs_kubectl
@pytest.mark.parametrize("overlay", OVERLAYS)
def test_probes_check_what_each_process_needs(rendered, overlay):
    docs = rendered[overlay]
    [api] = one(docs, "Deployment", "api")["spec"]["template"]["spec"]["containers"]
    assert api["readinessProbe"]["httpGet"]["path"] == "/health/ready"  # the database answers
    assert api["livenessProbe"]["httpGet"]["path"] == "/health"  # cheap: a slow database never restarts the process
    assert api["startupProbe"]["httpGet"]["path"] == "/health"
    assert api["lifecycle"]["preStop"]["exec"]["command"][0] == "sleep"
    [web] = one(docs, "Deployment", "web")["spec"]["template"]["spec"]["containers"]
    assert web["readinessProbe"]["httpGet"]["path"] == web["livenessProbe"]["httpGet"]["path"] == "/health"
    env = {e["name"]: e.get("value") for e in web["env"]}
    assert env["API_URL"] == "http://api.welcome-wave.svc.cluster.local:8080"  # nginx's resolver applies no search domains
    for d in find(docs, "Deployment"):
        if d["metadata"]["name"].endswith("-worker"):
            [w] = d["spec"]["template"]["spec"]["containers"]
            assert w["command"] == ["python", "-m", "workers.worker"]
            assert w["readinessProbe"]["httpGet"]["path"] == "/healthz" and w["ports"][0]["containerPort"] == 8081


@needs_kubectl
@pytest.mark.parametrize("overlay", OVERLAYS)
def test_the_migration_job_alone_holds_the_owner_password(rendered, overlay):
    docs = rendered[overlay]
    job = one(docs, "Job", "migrate")
    assert job["metadata"]["annotations"]["argocd.argoproj.io/hook"] == "PreSync"
    [c] = job["spec"]["template"]["spec"]["containers"]
    assert c["command"] == ["python", "-m", "app.migrate"] and c["image"] == "welcome-wave-api"
    assert c["envFrom"] == [{"secretRef": {"name": "ww-migrate"}}]
    for d in docs:
        if d["kind"] in POD_KINDS and d is not job:
            assert "ww-migrate" not in yaml.safe_dump(pod_spec(d)), d["metadata"]["name"]


@needs_kubectl
@pytest.mark.parametrize("overlay", OVERLAYS)
def test_ingress_routes_like_the_load_balancer_and_never_exposes_internal(rendered, overlay):
    docs = rendered[overlay]
    ing = one(docs, "Ingress", "welcome-wave")
    [rule] = ing["spec"]["rules"]
    assert rule["host"] == host_of(config(docs)["PUBLIC_URL"])
    routes = {p["path"]: (p["pathType"], p["backend"]["service"]["name"]) for p in rule["http"]["paths"]}
    assert routes == {"/api": ("Prefix", "api"), "/webhooks": ("Prefix", "api"), "/health/ready": ("Exact", "api"),
                      "/": ("Prefix", "web")}
    for tls in ing["spec"].get("tls", []):
        assert tls["hosts"] == [rule["host"]]


@needs_kubectl
@pytest.mark.parametrize("overlay", OVERLAYS)
def test_settings_are_ones_the_application_reads(rendered, overlay):
    known = {f.upper() for f in Settings.model_fields} | {f.upper() for f in AuditSettings.model_fields}
    known |= {f"RATE_LIMIT_{f.upper()}" for f in RateLimitSettings.model_fields}
    # read by the LangSmith client itself, and by the Cloud SQL Auth Proxy sidecar (components/gke)
    known |= {"LANGSMITH_ENDPOINT", "LANGSMITH_PROJECT", "CLOUD_SQL_INSTANCE"}
    cfg = config(rendered[overlay])
    assert set(cfg) <= known, set(cfg) - known
    assert cfg["ENV"] == "prod" and cfg["MODE"] == ("self_hosted" if overlay == "self-hosted" else "gcp")
    # every value parses; _env_file=None: never a developer's backend/.env, which holds real credentials
    settings = Settings(_env_file=None, **{k.lower(): v for k, v in cfg.items() if k.lower() in Settings.model_fields})
    assert settings.public_url.startswith("https://")


def test_secret_template_names_only_real_settings_and_holds_no_values():
    docs = [d for d in yaml.safe_load_all((K8S / "base/secrets.example.yaml").read_text()) if d]
    assert {d["metadata"]["name"] for d in docs} == {"ww-api", "ww-worker", "ww-migrate"}
    known = {f.upper() for f in Settings.model_fields} | {"DATABASE_URL_OWNER", "APP_DB_PASSWORD"}  # app/migrate.py
    known |= {"LANGSMITH_API_KEY"}  # read by the LangSmith client itself
    for d in docs:
        assert set(d["stringData"]) <= known, set(d["stringData"]) - known
        assert all(v == "" for v in d["stringData"].values())
    # never rendered, so applying an overlay can never overwrite a real Secret with an empty one
    for k in K8S.rglob("kustomization.yaml"):
        assert "secrets.example.yaml" not in yaml.safe_dump(yaml.safe_load(k.read_text())), k  # comments may point to it


# ---------------------------------------------------------------- scaling
@needs_kubectl
@pytest.mark.parametrize("overlay", OVERLAYS)
def test_autoscaled_deployments_name_no_replica_count(rendered, overlay):
    docs = rendered[overlay]
    scaled = {d["spec"]["scaleTargetRef"]["name"] for d in docs if d["kind"] in {"HorizontalPodAutoscaler", "ScaledObject"}}
    for d in find(docs, "Deployment"):
        name = d["metadata"]["name"]
        if name in scaled:
            assert "replicas" not in d["spec"], name  # a deploy must not reset what the autoscaler chose
    if overlay == "self-hosted":
        api = one(docs, "Deployment", "api")
        assert api["spec"]["replicas"] == 1 and api["spec"]["strategy"] == {"type": "Recreate"}
        volumes = {v["name"]: v for v in api["spec"]["template"]["spec"]["volumes"]}
        assert volumes["data"]["persistentVolumeClaim"]["claimName"] == "ww-data"
        assert one(docs, "PersistentVolumeClaim", "ww-data")["spec"]["accessModes"] == ["ReadWriteOnce"]
        assert not find(docs, "HorizontalPodAutoscaler", "api") and not find(docs, "PodDisruptionBudget", "api")
        # background work runs in the API: no Pub/Sub workers, no scheduled calls
        assert not [d for d in docs if d["kind"] in {"CronJob"} or d["metadata"]["name"].endswith("-worker")]
    else:
        assert {"api", "web"} <= scaled and {"meeting-worker", "ingestion-worker", "review-worker"} <= scaled
        hpa = one(docs, "HorizontalPodAutoscaler", "api")["spec"]
        assert hpa["maxReplicas"] <= 10  # each API pod holds up to 14 connections; the database allows 300 (dev) / 400 (prod)
    assert one(docs, "PodDisruptionBudget", "web")["spec"]["maxUnavailable"] == 1


@needs_kubectl
def test_production_spreads_three_api_pods_over_zones(rendered):
    docs = rendered["production"]
    assert one(docs, "HorizontalPodAutoscaler", "api")["spec"]["minReplicas"] == 3
    spread = one(docs, "Deployment", "api")["spec"]["template"]["spec"]["topologySpreadConstraints"]
    assert {s["topologyKey"] for s in spread} == {"topology.kubernetes.io/zone", "kubernetes.io/hostname"}


# ---------------------------------------------------------------- GKE
@needs_kubectl
@pytest.mark.parametrize("overlay", GKE)
def test_gke_identities_certificate_and_client_address_follow_the_settings(rendered, overlay):
    docs = rendered[overlay]
    cfg = config(docs)
    project = cfg["GCP_PROJECT"]
    assert cfg["OUTBOX_FLUSH_INVOKER"] == f"ww-scheduler@{project}.iam.gserviceaccount.com"
    accounts = {d["metadata"]["name"]: (d["metadata"].get("annotations") or {}) for d in find(docs, "ServiceAccount")}
    for name, gsa in {"api": "ww-api", "worker": "ww-worker", "migrate": "ww-migrate", "scheduler": "ww-scheduler"}.items():
        assert accounts[name]["iam.gke.io/gcp-service-account"] == f"{gsa}@{project}.iam.gserviceaccount.com"
    assert "iam.gke.io/gcp-service-account" not in accounts["web"]  # nginx calls no Google API
    assert one(docs, "ManagedCertificate", "ww-web")["spec"]["domains"] == [host_of(cfg["PUBLIC_URL"])]
    ing = one(docs, "Ingress", "welcome-wave")
    assert ing["metadata"]["annotations"]["kubernetes.io/ingress.class"] == "gce" and "tls" not in ing["spec"]
    assert cfg["RATE_LIMIT_TRUSTED_HOPS"] == "2"  # Google's load balancer appends "client, load balancer"
    assert cfg["OUTBOX_FLUSH_AUDIENCE"] and cfg["CLOUD_SQL_INSTANCE"].startswith(f"{project}:")
    assert one(docs, "BackendConfig", "api")["spec"]["timeoutSec"] >= 3600  # live-update streams last 15 minutes


@needs_kubectl
@pytest.mark.parametrize("overlay", GKE)
def test_gke_reaches_cloud_sql_through_the_same_pinned_proxy_as_the_workers_file(rendered, overlay):
    workers_file = [d for d in yaml.safe_load_all((K8S / "workers.yaml").read_text()) if d and d["kind"] == "Deployment"]
    pinned = workers_file[0]["spec"]["template"]["spec"]["initContainers"][0]["image"]
    docs = rendered[overlay]
    for name in ("api", "meeting-worker", "ingestion-worker", "review-worker", "migrate"):
        d = one(docs, "Job" if name == "migrate" else "Deployment", name)
        [proxy] = pod_spec(d)["initContainers"]
        assert proxy["image"] == pinned and proxy["restartPolicy"] == "Always"  # a native sidecar: lets the Job finish
        socket = "--unix-socket=/cloudsql" in proxy["args"]
        assert socket == (name in {"api", "migrate"})  # the URLs Cloud Run uses; the workers use 127.0.0.1:5432
        if socket:
            mounts = {m["mountPath"] for m in pod_spec(d)["containers"][0]["volumeMounts"]}
            assert "/cloudsql" in mounts


@needs_kubectl
@pytest.mark.parametrize("overlay", GKE)
def test_external_secrets_read_terraforms_secrets_as_their_own_workload(rendered, overlay):
    tf = (ROOT / "infra/terraform/secrets.tf").read_text()
    readers = dict(re.findall(r'"([a-z0-9-]+)"\s*=\s*\[([^\]]*)\]', tf[tf.index("readers = {"):tf.index("workload_emails")]))
    docs = rendered[overlay]
    store_identity = {d["metadata"]["name"]: d["spec"]["provider"]["gcpsm"]["auth"]["workloadIdentity"]["serviceAccountRef"]["name"]
                      for d in find(docs, "SecretStore")}
    for d in docs:
        assert d["spec"]["provider"]["gcpsm"]["projectID"] == config(docs)["GCP_PROJECT"] if d["kind"] == "SecretStore" else True
    for es in find(docs, "ExternalSecret"):
        workload = store_identity[es["spec"]["secretStoreRef"]["name"]]
        for item in es["spec"]["data"]:
            name = item["remoteRef"]["key"].removeprefix("ww-")
            assert name in readers, name
            assert f'"{workload}"' in readers[name], f"{workload} cannot read ww-{name} (infra/terraform/secrets.tf)"


# ---------------------------------------------------------------- scheduled calls
def call_module():
    spec = importlib.util.spec_from_file_location("ww_scheduler_call", K8S / "base/scheduler/call.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@needs_kubectl
def test_scheduled_calls_match_terraforms_jobs_and_the_apis_routes(rendered):
    from app.main import app

    tf = (ROOT / "infra/terraform/events.tf").read_text()
    blocks = re.findall(r'resource "google_cloud_scheduler_job"[^{]*\{(.*?)\n\}', tf, re.S)
    terraform = {re.search(r'uri\s*=\s*"\$\{local\.api_url\}(\S+)"', b).group(1): re.search(r'schedule\s*=\s*"([^"]+)"', b).group(1)
                 for b in blocks}
    cron = {}
    for d in find(rendered["production"], "CronJob"):
        [c] = pod_spec(d)["containers"]
        assert c["command"][:2] == ["python", "/scripts/call.py"]
        cron[c["command"][2]] = d["spec"]["schedule"]
        assert d["spec"]["timeZone"] == "Etc/UTC" and d["spec"]["concurrencyPolicy"] == "Forbid"
        assert pod_spec(d)["serviceAccountName"] == "scheduler"
    assert cron == terraform
    served = {path for path, ops in app.openapi()["paths"].items() if "post" in ops}
    assert set(cron) <= served and set(cron) == set(call_module().PATHS)


class _Recorder(http.server.BaseHTTPRequestHandler):
    seen: list = []
    status = 200

    def do_POST(self):  # noqa: N802 - http.server API
        _Recorder.seen.append((self.path, self.headers.get("Authorization")))
        self.send_response(_Recorder.status)
        self.end_headers()
        self.wfile.write(b'{"sent": 0}')

    def log_message(self, *_):
        pass


@pytest.fixture
def api_stub():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Recorder.seen, _Recorder.status = [], 200
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_scheduled_call_sends_a_google_token_for_the_audience_and_fails_loudly(api_stub, monkeypatch):
    mod = call_module()
    audiences = []
    monkeypatch.setattr(mod, "id_token", lambda aud: audiences.append(aud) or "tok")
    env = {"OUTBOX_FLUSH_AUDIENCE": "https://app.example.com", "WW_API_URL": api_stub, "WW_CALL_TIMEOUT_S": "5"}
    assert mod.call("/internal/outbox/flush", env) == 0
    assert _Recorder.seen == [("/internal/outbox/flush", "Bearer tok")] and audiences == ["https://app.example.com"]
    _Recorder.status = 503  # the flush answers 503 while events wait: the Job must fail, so it shows
    assert mod.call("/internal/outbox/flush", env) == 1
    assert mod.call("/api/anything", env) == 2 and len(_Recorder.seen) == 2  # only the four scheduled calls, never sent
    assert mod.call("/internal/audit/purge", env | {"WW_API_URL": "http://127.0.0.1:9"}) == 1  # nothing listening
    assert mod.call("/internal/outbox/flush", {"WW_API_URL": api_stub}) == 2  # no audience: the API has the calls off

    def no_identity(_aud):
        raise RuntimeError("no metadata server")

    monkeypatch.setattr(mod, "id_token", no_identity)  # no Workload Identity binding: a clear failure, not a traceback
    assert mod.call("/internal/outbox/flush", env) == 1 and len(_Recorder.seen) == 2


# ---------------------------------------------------------------- deploy.sh
def deploy_sh(*args: str) -> subprocess.CompletedProcess:
    env = os.environ | ({"KUBECTL": KUBECTL} if KUBECTL else {})
    return subprocess.run(["bash", str(K8S / "deploy.sh"), *args], capture_output=True, text=True, env=env, timeout=60)


API = "asia-south1-docker.pkg.dev/p/ww-images/backend@sha256:" + "a" * 64
WEB = "asia-south1-docker.pkg.dev/p/ww-images/web@sha256:" + "b" * 64


@needs_kubectl
@pytest.mark.parametrize("overlay", OVERLAYS)
def test_deploy_renders_the_release_images_everywhere(overlay):
    out = deploy_sh(overlay, "--api-image", API, "--web-image", WEB, "--render-only")
    assert out.returncode == 0, out.stderr
    docs = [d for d in yaml.safe_load_all(out.stdout) if d]
    images = {c["image"] for d in docs if d["kind"] in POD_KINDS for c in pod_spec(d)["containers"]}
    assert images == {API, WEB}
    assert "warning" not in out.stderr  # both by digest


def test_deploy_refuses_what_it_cannot_release():
    assert deploy_sh("staging", "--api-image", "backend", "--web-image", WEB, "--render-only").returncode != 0
    assert deploy_sh("nowhere", "--api-image", API, "--web-image", WEB, "--render-only").returncode != 0
    assert deploy_sh("staging", "--render-only").returncode == 2  # usage


@needs_kubectl
def test_deploy_runs_the_migration_alone_between_the_foundations_and_the_new_code(rendered):
    out = deploy_sh("production", "--api-image", API, "--web-image", WEB, "--render-only")
    stream = out.stdout

    def kinds(selector: str, keep: int) -> list[str]:
        r = subprocess.run(["bash", "-c", f'source "{K8S / "deploy.sh"}"; select_kinds "$1" "$2"', "_", selector, str(keep)],
                           input=stream, capture_output=True, text=True, timeout=30)
        return [d["kind"] for d in yaml.safe_load_all(r.stdout) if d]

    first, migration, last = kinds("Job|CronJob|Deployment", 0), kinds("Job", 1), kinds("CronJob|Deployment", 1)
    assert migration == ["Job"]
    assert not {"Job", "CronJob", "Deployment"} & set(first) and set(last) == {"CronJob", "Deployment"}
    assert len(first) + len(migration) + len(last) == len([d for d in yaml.safe_load_all(stream) if d])  # nothing lost


# ---------------------------------------------------------------- images and docker compose
def test_images_declare_their_port_health_and_unprivileged_user():
    api, web = (ROOT / "backend/Dockerfile").read_text(), (ROOT / "frontend/Dockerfile").read_text()
    for text in (api, web):
        assert "EXPOSE 8080" in text and "HEALTHCHECK" in text and "org.opencontainers.image.revision" in text
        assert text.count("FROM ") == 2  # build stage, then only what runs
    assert "USER 10001" in api and "USER nginx" in web
    # the web pod's runAsUser is nginx's uid in the image
    assert "uid 101" in web and "runAsUser: 101" in (K8S / "base/web.yaml").read_text()
    for ignore in (ROOT / "backend/.dockerignore", ROOT / "frontend/.dockerignore"):
        lines = set(ignore.read_text().split())
        assert {".env", ".env.*", "*.pem"} <= lines, ignore


def test_compose_runs_the_demo_hardened_and_on_this_machine_only():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    services = compose["services"]
    assert set(services) == {"api", "web", "db"} and services["db"]["profiles"] == ["db"]
    for name in ("api", "web"):
        s = services[name]
        assert s["read_only"] is True and s["cap_drop"] == ["ALL"] and "no-new-privileges:true" in s["security_opt"]
        assert s["build"]["dockerfile"] == "Dockerfile" and "healthcheck" in s
    for s in services.values():
        assert all(str(p).startswith("127.0.0.1:") for p in s.get("ports", []))
    assert services["api"]["environment"]["MODE"] == "demo"  # demo mode trusts X-Demo-User: never anything else here
    assert services["web"]["depends_on"] == {"api": {"condition": "service_healthy"}}
    assert services["web"]["environment"]["API_URL"] == "http://api:8080"
