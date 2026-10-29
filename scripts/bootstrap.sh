#!/usr/bin/env bash
# Create the bucket, dataset, tables and service account.
#
# Idempotent: every statement is IF NOT EXISTS or tolerates an existing
# resource, so re-running after a partial failure is safe.
set -euo pipefail

: "${PROJECT:?set PROJECT}"
REGION="${REGION:-us-east4}"
DATASET="${DATASET:-doc_extract}"
BUCKET="${RAW_BUCKET:-${PROJECT}-docs-raw}"
SA="doc-extract@${PROJECT}.iam.gserviceaccount.com"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"

gcloud config set project "$PROJECT" >/dev/null

echo "==> enabling services"
# pubsub is not optional: Eventarc delivers storage events over it, and a
# trigger fails to build without it.
gcloud services enable \
  run.googleapis.com aiplatform.googleapis.com eventarc.googleapis.com \
  bigquery.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
  storage.googleapis.com pubsub.googleapis.com

echo "==> raw bucket gs://${BUCKET}"
gcloud storage buckets describe "gs://${BUCKET}" >/dev/null 2>&1 \
  || gcloud storage buckets create "gs://${BUCKET}" --location="$REGION"
# raw/ is the system of record. Versioning is what makes "immutable" true
# rather than merely intended.
gcloud storage buckets update "gs://${BUCKET}" --versioning

echo "==> dataset ${DATASET}"
bq --location="$REGION" mk --dataset --force "${PROJECT}:${DATASET}" >/dev/null

echo "==> tables and view"
DATASET="$DATASET" python3 sql/generate.py
for f in sql/0*.sql; do
  echo "    $f"
  bq query --use_legacy_sql=false --project_id="$PROJECT" < "$f"
done

echo "==> service account"
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 \
  || gcloud iam service-accounts create doc-extract --display-name="doc-extract"

# Note what is absent: no storage.objectAdmin, no storage.objectCreator. The
# service reads raw and can never modify the source it reads from, which is
# the property that makes a re-run safe to repeat.
for ROLE in roles/aiplatform.user roles/bigquery.dataEditor roles/storage.objectViewer; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:${SA}" --role="$ROLE" --condition=None >/dev/null
done

echo "==> eventarc needs the service account to receive events"
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:${SA}" --role="roles/eventarc.eventReceiver" --condition=None >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:${SA}" --role="roles/run.invoker" --condition=None >/dev/null

# Three separate agents have to be right before a storage trigger will build,
# and the error you get when one is missing names only the first of them.
echo "==> eventarc service agent"
# Enabling the API does not reliably create this agent. Force it, then grant
# its role -- without both, trigger creation fails FAILED_PRECONDITION.
gcloud beta services identity create \
  --service=eventarc.googleapis.com --project="$PROJECT" >/dev/null 2>&1 || true
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-eventarc.iam.gserviceaccount.com" \
  --role="roles/eventarc.serviceAgent" --condition=None >/dev/null

echo "==> cloud storage service agent publishes the object events"
# tr, because this command emits leading whitespace and a newline. Captured
# raw, the member argument carries them and the API reports the account as
# nonexistent -- which sends you looking for a provisioning problem that is
# not there.
GCS_SA="$(gcloud storage service-agent --project="$PROJECT" | tr -d '[:space:]')"
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:${GCS_SA}" \
  --role="roles/pubsub.publisher" --condition=None >/dev/null

cat <<MSG

Done.
  bucket   gs://${BUCKET}   (versioned, read-only to the service)
  dataset  ${PROJECT}:${DATASET}
  account  ${SA}

IAM bindings for the service agents can take a minute or two to propagate.
If `deploy.sh` reports FAILED_PRECONDITION on the trigger, wait and re-run it;
it retries on its own.

Set a budget alert before you run anything against Vertex:
  https://console.cloud.google.com/billing/budgets
MSG
