#!/bin/bash
# GitHub Actions Deployment Script for Cloud Run
# This script deploys the FastAPI application to Google CloudRun

set -e  # Exit on any error

echo "========================================="
echo "Starting Cloud Run Deployment"
echo "========================================="

# Load environment variables from .env file if exists
if [ -e .env ]; then
    source .env
    ENVS=$(grep -v '^#' .env | xargs)
    echo "Loaded .env file"
else
    ENVS=""
    echo "No .env file found"
fi

# Check PROJECT_ID
if [ ! "$PROJECT_ID" ]; then
    PROJECT_ID=$(gcloud config get-value project)
    if [ ! "$PROJECT_ID" ]; then
        echo "ERROR: PROJECT_ID must be set using gcloud CLI, or must be specified as an environment variable."
        exit 1
    fi
fi
echo "PROJECT_ID: ${PROJECT_ID}"

# Get PROJECT_NUMBER
PROJECT_NUMBER=$(gcloud projects list --filter="$PROJECT_ID" --format='value("PROJECT_NUMBER")')
if [ ! "${PROJECT_NUMBER}" ]; then
    echo "ERROR: Could not find PROJECT_NUMBER for PROJECT_ID: ${PROJECT_ID}"
    exit 1
fi
echo "PROJECT_NUMBER: ${PROJECT_NUMBER}"

# Add PROJECT_ID and PROJECT_NUMBER to environment variables
ENVS=${ENVS}" PROJECT_ID=$PROJECT_ID PROJECT_NUMBER=${PROJECT_NUMBER}"

# Check required variables
if [ ! "${SERVICE_NAME}" ]; then
    echo "ERROR: SERVICE_NAME must be specified as an environment variable."
    exit 1
fi
echo "SERVICE_NAME: ${SERVICE_NAME}"

# Set defaults for optional variables
if [ ! "${GCP_REGION}" ]; then
    GCP_REGION="us-east1"
fi
echo "GCP_REGION: ${GCP_REGION}"

if [ ! "${PORT}" ]; then
    PORT=8000
fi
echo "PORT: ${PORT}"

# Cap scale-out so session-mode clients (1 LISTEN + Direct pool per instance)
# stay well below Supavisor session pool_size (EMAXCONNSESSION).
# Budget ≈ max_instances × 4 ≪ 80 → default 15 leaves headroom for other clients.
# Values are loaded from Secret Manager unless already set in the environment.
CLOUD_RUN_CONCURRENCY_SECRET="${CLOUD_RUN_CONCURRENCY_SECRET:-cloud-run-concurrency}"
CLOUD_RUN_MAX_INSTANCES_SECRET="${CLOUD_RUN_MAX_INSTANCES_SECRET:-cloud-run-max-instances}"

if [ ! "${CLOUD_RUN_CONCURRENCY}" ]; then
    if VAL=$(gcloud secrets versions access latest \
        --secret="${CLOUD_RUN_CONCURRENCY_SECRET}" \
        --project="${PROJECT_ID}" 2>/dev/null); then
        CLOUD_RUN_CONCURRENCY=$(echo "${VAL}" | tr -d '[:space:]')
    else
        echo "WARNING: Could not load secret '${CLOUD_RUN_CONCURRENCY_SECRET}', using default 10"
        CLOUD_RUN_CONCURRENCY=10
    fi
fi
echo "CLOUD_RUN_CONCURRENCY: ${CLOUD_RUN_CONCURRENCY}"

if [ ! "${CLOUD_RUN_MAX_INSTANCES}" ]; then
    if VAL=$(gcloud secrets versions access latest \
        --secret="${CLOUD_RUN_MAX_INSTANCES_SECRET}" \
        --project="${PROJECT_ID}" 2>/dev/null); then
        CLOUD_RUN_MAX_INSTANCES=$(echo "${VAL}" | tr -d '[:space:]')
    else
        echo "WARNING: Could not load secret '${CLOUD_RUN_MAX_INSTANCES_SECRET}', using default 15"
        CLOUD_RUN_MAX_INSTANCES=15
    fi
fi
echo "CLOUD_RUN_MAX_INSTANCES: ${CLOUD_RUN_MAX_INSTANCES}"

# Get the tag from GitHub Actions input
if [ ! "${IMAGE_TAG}" ]; then
    echo "ERROR: IMAGE_TAG is not set. This should be passed from the GitHub Actions workflow."
    exit 1
fi
echo "IMAGE_TAG: ${IMAGE_TAG}"

# Check if VPC_CONNECTOR is set
if [ "${VPC_CONNECTOR}" ]; then
    VPC_CONNECTOR_FLAG="--vpc-connector ${VPC_CONNECTOR}"
    echo "VPC_CONNECTOR: ${VPC_CONNECTOR}"
else
    VPC_CONNECTOR_FLAG=""
    echo "No VPC_CONNECTOR configured"
fi

# Construct the full image path
IMAGE_PATH="gcr.io/${PROJECT_ID}/${GCR_REPO}/${IMAGE_TAG}:${IMAGE_TAG}"
echo "Deploying image: ${IMAGE_PATH}"

# Deploy to Cloud Run
echo "========================================="
echo "Deploying to Cloud Run..."
echo "========================================="

gcloud run deploy "${SERVICE_NAME}" \
    --image "${IMAGE_PATH}" \
    --platform managed \
    --set-env-vars="${ENVS// /,}" \
    --port ${PORT} \
    --concurrency "${CLOUD_RUN_CONCURRENCY}" \
    --region ${GCP_REGION} \
    --allow-unauthenticated \
    ${VPC_CONNECTOR_FLAG} \
    --min-instances=1 \
    --max-instances="${CLOUD_RUN_MAX_INSTANCES}"

# Update traffic to latest revision
echo "========================================="
echo "Updating traffic to latest revision..."
echo "========================================="

gcloud run services update-traffic "${SERVICE_NAME}" \
    --to-latest \
    --region ${GCP_REGION}

echo "========================================="
echo "Deployment completed successfully!"
echo "Service URL: $(gcloud run services describe ${SERVICE_NAME} --platform managed --region ${GCP_REGION} --format='value(status.url)')"
echo "========================================="
