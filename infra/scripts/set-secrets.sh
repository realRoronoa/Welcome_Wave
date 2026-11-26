#!/usr/bin/env bash
# Add the third-party secrets Terraform cannot generate. Values are read from your terminal (not echoed, not
# written to disk or shell history) and stored as new versions in Secret Manager.
#   PROJECT_ID=welcome-wave-dev ./infra/scripts/set-secrets.sh
#   PROJECT_ID=... ./infra/scripts/set-secrets.sh github-app-private-key=path/to/key.pem calendly-webhook-signing-key=unused  # file or literal
# smtp-password: the mail server password (with _SMTP_HOST and _SMTP_USER in build_substitutions); Terraform starts it as "unused".
set -euo pipefail
: "${PROJECT_ID:?set PROJECT_ID}"
SECRETS=(github-app-private-key github-webhook-secret github-client-secret google-oauth-client-secret calendly-webhook-signing-key langsmith-api-key smtp-password)

put() { # name, value-from-stdin
  gcloud secrets versions add "ww-$1" --project "$PROJECT_ID" --data-file=- >/dev/null && echo "  ww-$1 updated"
}

if [[ $# -gt 0 ]]; then
  # Check every argument before storing any: a mistyped key path must fail, not become the key.
  for pair in "$@"; do
    name="${pair%%=*}" value="${pair#*=}"
    value="${value/#\~/$HOME}"  # the shell does not expand ~ after name=
    if [[ "$pair" != *=* ]]; then echo "expected name=value, got '$pair'" >&2; exit 1; fi
    if [[ " ${SECRETS[*]} " != *" $name "* ]]; then echo "unknown secret '$name' (one of: ${SECRETS[*]})" >&2; exit 1; fi
    if [[ "$name" == "github-app-private-key" && "$value" != "unused" && ! -f "$value" ]]; then
      echo "no such file: $value (github-app-private-key takes the path of the .pem, or 'unused')" >&2; exit 1
    fi
  done
  for pair in "$@"; do
    name="${pair%%=*}" value="${pair#*=}"
    value="${value/#\~/$HOME}"
    if [[ -f "$value" ]]; then put "$name" < "$value"; else printf '%s' "$value" | put "$name"; fi  # a file, or a literal like "unused"
  done
  exit 0
fi

for name in "${SECRETS[@]}"; do
  if [[ "$name" == "github-app-private-key" ]]; then
    while true; do
      read -r -p "Path to the GitHub App private key (.pem), 'unused', or empty to skip: " path
      path="${path/#\~/$HOME}"  # read does not expand ~/Downloads/...
      if [[ -z "$path" || "$path" == "unused" || -f "$path" ]]; then break; fi
      echo "  no such file: $path"
    done
    if [[ "$path" == "unused" ]]; then printf 'unused' | put "$name"; elif [[ -n "$path" ]]; then put "$name" < "$path"; fi
    continue
  fi
  read -r -s -p "$name (empty to skip): " value; echo
  if [[ -n "$value" ]]; then printf '%s' "$value" | put "$name"; fi
done
