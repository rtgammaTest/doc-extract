#!/usr/bin/env bash
# Deploy the Cloud Run service and wire the Eventarc trigger to the raw bucket.
set -euo pipefail

: "${PROJECT:?set PROJECT}"
REGION="${REGION:-us-east4}"
DATASET="${DATASET:-doc_extract}"
BUCKET="${RAW_BUCKET:-${PROJECT}-docs-raw}"
SA="doc-extract@${PROJECT}.iam.gserviceaccount.com"
VERSION="${EXTRACTOR_VERSION:-1.0.0}"
MODEL_ID="${MODEL_ID:-gemini-3.8-flash}"
# Not REGION. Gemini is not served everywhere Cloud Run is.
VERTEX_LOCATION="${VERTEX_LOCATION:-us-central1}"
# "vertex", "gemini-api" or "fake". See README for what each one needs.
MODEL_BACKEND="${MODEL_BACKEND:-vertex}"
GEMINI_API_KEY="${GEMINI_API_KEY:-}"

echo "==> deploying doc-extract ${VERSION}"
gcloud run deploy doc-extract \
  --source . \
  --region "$REGION" \
  --service-account "$SA" \
  --no-allow-unauthenticated \
  --set-env-vars "PROJECT=${PROJECT},REGION=${REGION},DATASET=${DATASET},RAW_BUCKET=${BUCKET},EXTRACTOR_VERSION=${VERSION},MODEL_ID=${MODEL_ID},VERTEX_LOCATION=${VERTEX_LOCATION},MODEL_BACKEND=${MODEL_BACKEND},GEMINI_API_KEY=${GEMINI_API_KEY},STORE_BACKEND=bigquery"

echo "==> eventarc trigger"
# Service-agent IAM propagates on its own schedule, and the resulting
# FAILED_PRECONDITION looks like a misconfiguration rather than a wait. Retry
# rather than making the operator guess which one it is.
if gcloud eventarc triggers describe doc-extract-trigger --location="$REGION" >/dev/null 2>&1; then
  echo "    trigger already exists"
else
  for attempt in 1 2 3 4; do
    if gcloud eventarc triggers create doc-extract-trigger \
      --location="$REGION" \
      --destination-run-service=doc-extract \
      --destination-run-region="$REGION" \
      --event-filters="type=google.cloud.storage.object.v1.finalized" \
      --event-filters="bucket=${BUCKET}" \
      --service-account="$SA"; then
      break
    fi
    if [ "$attempt" -eq 4 ]; then
      echo
      echo "Trigger creation failed four times. If the error mentions the Eventarc"
      echo "Service Agent, re-run scripts/bootstrap.sh -- it grants the two agent"
      echo "roles that a storage trigger needs." >&2
      exit 1
    fi
    echo "    attempt ${attempt} failed; waiting 45s for IAM to propagate"
    sleep 45
  done
fi

echo
echo "Drop a document in to extract it:"
echo "  gcloud storage cp agreement.txt gs://${BUCKET}/raw/"
