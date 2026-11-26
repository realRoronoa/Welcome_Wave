#!/usr/bin/env bash
# Release Welcome Wave to a Kubernetes cluster from one of infra/k8s/overlays, in the only safe order:
#   1. render the overlay with the two images (kubectl kustomize; nothing changes yet)
#   2. apply what runs no new code: the namespace, service accounts, settings, Secret sources, Services, the Ingress,
#      autoscalers, disruption budgets and network policies
#   3. check that the Secrets the pods read exist (with the External Secrets Operator: wait until they have synced)
#   4. run the database migration Job with the new image and wait for it. If it fails, stop here: the running release is
#      untouched, and the Job's log is printed
#   5. roll out the API, the web app, the workers and the scheduled jobs, and wait until each is ready
#
#   infra/k8s/deploy.sh <overlay> --api-image IMAGE --web-image IMAGE [--context NAME] [--timeout 10m] [--yes]
#   infra/k8s/deploy.sh <overlay> --api-image IMAGE --web-image IMAGE --render-only     # print, change nothing
#
#   <overlay>      staging, production or self-hosted (a folder in infra/k8s/overlays), or the path of your own overlay
#   IMAGE          a full image reference, best by digest (registry/name@sha256:...), so the release is exactly what was
#                  tested; the API image also runs the workers, the migration and the scheduled calls
#   --context      the kubectl context to release to (default: the current one; it is printed before anything changes)
#   --yes          do not ask before changing the cluster (CI); asked only when run in a terminal
#   --render-only  print the manifests that would be applied, with the images set; needs no cluster
#
# Roll back by running it again with the previous images (migrations only ever add, so the previous release runs on the
# newer schema). docs/KUBERNETES.md has the whole story.
set -euo pipefail

K8S="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NS=welcome-wave
KUBECTL="${KUBECTL:-kubectl}"
KARGS=()

say() { printf '\033[1m%s\033[0m\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
k() { "$KUBECTL" ${KARGS[@]+"${KARGS[@]}"} "$@"; }

usage() { awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "${BASH_SOURCE[0]}" >&2; exit 2; }

# The documents of a multi-document YAML stream whose top-level kind matches (keep=1) or does not match (keep=0) a regular
# expression. kubectl kustomize writes every document's `kind:` at the start of a line and separates documents with `---`.
select_kinds() {
  awk -v kinds="^($1)\$" -v keep="$2" '
    function flush() { if (body != "") { if ((kind ~ kinds) == keep) printf "---\n%s", body } body = ""; kind = "" }
    /^---$/ { flush(); next }
    { body = body $0 "\n"; if ($0 ~ /^kind: /) kind = substr($0, 7) }
    END { flush() }'
}

# The names of the objects of one kind in a YAML stream (top-level metadata.name)
names_of() {
  select_kinds "$1" 1 | awk '/^metadata:/ { m = 1; next } m && /^  name: / { print substr($0, 9); m = 0 } /^[^ ]/ { m = 0 }'
}

image_ok() { [[ "$1" == */* && ( "$1" == *@sha256:* || "$1" =~ :[A-Za-z0-9_.-]+$ ) ]]; }

# The overlay, rendered, with welcome-wave-api and welcome-wave-web replaced by the release's images
render() {
  local overlay="$1" api="$2" web="$3" out
  out="$(k kustomize "$overlay")" || die "kubectl kustomize $overlay failed"
  grep -qE '^ *image: welcome-wave-api$' <<<"$out" \
    || die "$overlay sets the API image itself; set it there, or remove its images: block and pass --api-image"
  out="$(sed -E -e "s#^( *)image: welcome-wave-api\$#\\1image: ${api}#" -e "s#^( *)image: welcome-wave-web\$#\\1image: ${web}#" <<<"$out")"
  if grep -qE '^ *image: welcome-wave-(api|web)$' <<<"$out"; then die "an image was left unset"; fi
  printf '%s\n' "$out"
}

wait_for_job() { # name, timeout in seconds: 0 when it succeeded, 1 when it failed or ran out of time
  local job="$1" limit="$2" waited=0 ok bad
  while [ "$waited" -lt "$limit" ]; do
    ok="$(k -n "$NS" get job "$job" -o jsonpath='{.status.succeeded}' 2>/dev/null || true)"
    bad="$(k -n "$NS" get job "$job" -o jsonpath='{.status.failed}' 2>/dev/null || true)"
    if [ "${ok:-0}" -ge 1 ]; then return 0; fi
    if [ "${bad:-0}" -ge 1 ]; then return 1; fi
    sleep 5; waited=$((waited + 5))
  done
  return 1
}

seconds() { case "$1" in *m) echo $(( ${1%m} * 60 )) ;; *s) echo "${1%s}" ;; *) echo "$1" ;; esac; }

main() {
  local overlay="" api="" web="" render_only="" yes="" timeout="10m"
  while [ $# -gt 0 ]; do
    case "$1" in
      --api-image) api="${2:-}"; shift 2 ;;
      --web-image) web="${2:-}"; shift 2 ;;
      --context) KARGS+=(--context "${2:-}"); shift 2 ;;
      --timeout) timeout="${2:-}"; shift 2 ;;
      --render-only) render_only=1; shift ;;
      --yes) yes=1; shift ;;
      -h | --help) usage ;;
      -*) die "unknown option $1 (--help)" ;;
      *) [ -z "$overlay" ] || die "one overlay at a time"; overlay="$1"; shift ;;
    esac
  done
  [ -n "$overlay" ] && [ -n "$api" ] && [ -n "$web" ] || usage
  [ -d "$overlay" ] || overlay="$K8S/overlays/$overlay"
  [ -f "$overlay/kustomization.yaml" ] || die "no overlay at $overlay"
  for i in "$api" "$web"; do image_ok "$i" || die "$i is not a full image reference (registry/name@sha256:... or :tag)"; done
  [[ "$api" == *@sha256:* && "$web" == *@sha256:* ]] || say "warning: an image is given by tag; a digest pins exactly what was tested"
  [[ "$timeout" =~ ^[0-9]+[sm]?$ ]] || die "--timeout takes seconds or minutes, such as 600 or 10m"

  local all
  all="$(render "$overlay" "$api" "$web")"
  if [ -n "$render_only" ]; then printf '%s\n' "$all"; return 0; fi

  local context server
  context="$(k config current-context 2>/dev/null)" || die "kubectl has no current context (--context NAME)"
  for ((i = 0; i < ${#KARGS[@]}; i++)); do [ "${KARGS[$i]}" = --context ] && context="${KARGS[$((i + 1))]}"; done
  server="$(k config view --minify -o jsonpath='{.clusters[0].cluster.server}' 2>/dev/null || true)"
  say "Releasing $(basename "$overlay") to context $context ($server), namespace $NS"
  say "  api image: $api"
  say "  web image: $web"
  if [ -z "$yes" ] && [ -t 0 ]; then
    read -r -p "Go ahead? [y/N] " answer
    [[ "$answer" =~ ^[Yy] ]] || die "nothing changed"
  fi

  say "1/4 Settings, identities, services, policies"
  select_kinds 'Job|CronJob|Deployment' 0 <<<"$all" | k apply -f -

  say "2/4 Secrets"
  if grep -q '^kind: ExternalSecret$' <<<"$all"; then
    k -n "$NS" wait --for=condition=Ready externalsecret --all --timeout=180s \
      || die "the External Secrets did not sync: kubectl -n $NS describe externalsecret (docs/KUBERNETES.md, \"Secrets\")"
  fi
  local s need=(ww-api ww-migrate)
  if grep -q 'name: ww-worker$' <<<"$all"; then need+=(ww-worker); fi
  for s in "${need[@]}"; do
    k -n "$NS" get secret "$s" >/dev/null 2>&1 \
      || die "the Secret $s is missing: create it first (infra/k8s/base/secrets.example.yaml); nothing has started"
  done

  say "3/4 Database migrations (the running release keeps serving)"
  k -n "$NS" delete job migrate --ignore-not-found --wait=true >/dev/null
  select_kinds 'Job' 1 <<<"$all" | k apply -f -
  if ! wait_for_job migrate 900; then
    k -n "$NS" logs job/migrate -c migrate --tail=80 >&2 || true
    die "the migration failed or did not finish (its log is above; kubectl -n $NS describe job migrate). Nothing new was \
rolled out: the running release is unchanged. Fix the cause and run this again."
  fi

  say "4/4 The API, the web app, the workers and the scheduled jobs"
  select_kinds 'CronJob|Deployment' 1 <<<"$all" | k apply -f -
  local d failed=""
  for d in $(names_of Deployment <<<"$all"); do
    k -n "$NS" rollout status "deploy/$d" --timeout="$(seconds "$timeout")s" || failed="$failed $d"
  done
  [ -z "$failed" ] || die "not ready in time:$failed. kubectl -n $NS describe deploy$failed; to go back, run this again with the previous images"
  say "Released. kubectl -n $NS get pods; the app: $(grep -m1 -E '^  PUBLIC_URL: ' <<<"$all" | sed 's/^  PUBLIC_URL: //')"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then main "$@"; fi
