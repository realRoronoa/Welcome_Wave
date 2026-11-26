#!/usr/bin/env bash
# One-time setup of the GKE Autopilot cluster: install KEDA and let it read Pub/Sub metrics with Workload Identity.
#   PROJECT_ID=welcome-wave-dev ENV=dev ./infra/scripts/bootstrap-cluster.sh
# Choose a KEDA release whose supported Kubernetes range includes your cluster's version
# (https://keda.sh/docs/latest/operate/cluster/#kubernetes-compatibility); the script prints the cluster version.
set -euo pipefail
: "${PROJECT_ID:?set PROJECT_ID}" "${ENV:?set ENV (dev|prod)}"
REGION="${REGION:-asia-south1}"
KEDA_VERSION="${KEDA_VERSION:-2.17.0}"

gcloud container clusters get-credentials "ww-workers-${ENV}" --region "$REGION" --project "$PROJECT_ID"
echo "Cluster version: $(kubectl version -o json | python3 -c 'import json,sys; print(json.load(sys.stdin)["serverVersion"]["gitVersion"])')"
echo "Installing KEDA ${KEDA_VERSION}"

helm repo add kedacore https://kedacore.github.io/charts >/dev/null
helm repo update kedacore >/dev/null
helm upgrade --install keda kedacore/keda --namespace keda --create-namespace --version "$KEDA_VERSION" \
  --set podIdentity.gcp.enabled=true \
  --set "serviceAccount.operator.annotations.iam\.gke\.io/gcp-service-account=ww-keda@${PROJECT_ID}.iam.gserviceaccount.com" \
  --wait

kubectl -n keda rollout status deploy/keda-operator --timeout=180s
echo "KEDA is ready. Cloud Build applies infra/k8s/workers.yaml on every deploy."
