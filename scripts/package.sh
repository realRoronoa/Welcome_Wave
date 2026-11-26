#!/usr/bin/env bash
# Zip the repository for sharing: the files git knows about (tracked, staged, and new files that .gitignore does not
# exclude), never secrets or build output, whatever .gitignore says.
#
#   ./scripts/package.sh                 -> dist/welcome-wave-<date>-<time>.zip (dist/ is git-ignored)
#   ./scripts/package.sh path/to/out.zip
#
# Always left out: .env and .env.* (except .env.example), selfhost.env, *.pem, and anything under .blobs, .data,
# node_modules, .venv, dist or __pycache__, and *.tsbuildinfo. The finished zip is checked for them before the script reports
# success.
set -euo pipefail

ROOT="$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel)"
OUT="${1:-$ROOT/dist/welcome-wave-$(date +%Y%m%d-%H%M%S).zip}"
case "$OUT" in /*) ;; *) OUT="$PWD/$OUT" ;; esac
command -v zip >/dev/null || { echo "package.sh: needs the zip command" >&2; exit 1; }

excluded() {
  local path="$1" name="${1##*/}" part
  case "$name" in
    .env.example) ;;
    .env | .env.* | selfhost.env | *.pem | *.tsbuildinfo | *.pyc) return 0 ;;
  esac
  IFS=/ read -r -a parts <<<"$path"
  for part in "${parts[@]}"; do
    case "$part" in .blobs | .data | node_modules | .venv | dist | __pycache__) return 0 ;; esac
  done
  return 1
}

LIST="$(mktemp)"
trap 'rm -f "$LIST"' EXIT
cd "$ROOT"
while IFS= read -r f; do
  [ -f "$f" ] || continue # in the index but deleted from the working tree
  excluded "$f" || printf '%s\n' "$f"
done < <(git ls-files --cached --others --exclude-standard | sort -u) >"$LIST"

COUNT="$(wc -l <"$LIST" | tr -d ' ')"
[ "$COUNT" -gt 0 ] || { echo "package.sh: nothing to package" >&2; exit 1; }
mkdir -p "$(dirname "$OUT")"
rm -f "$OUT"
zip -q -X "$OUT" -@ <"$LIST"

# the zip itself, checked: a secret or build output in it is an error, not a warning
LEAKED="$(zipinfo -1 "$OUT" | while IFS= read -r f; do excluded "$f" && printf '%s\n' "$f"; done || true)"
if [ -n "$LEAKED" ]; then
  echo "package.sh: refusing, the zip contains files that must never be shared:" >&2
  echo "$LEAKED" >&2
  rm -f "$OUT"
  exit 1
fi
echo "$OUT"
echo "$COUNT files"
