# #!/bin/bash
# This script is used to build, submit the FastAPI application docker image to the Google Container Registry,
# and then deploy it as a Google CloudRun Application


gcloud config set builds/use_kaniko True
gcloud config set builds/kaniko_cache_ttl 168


if [ -e .env ]; then
    source .env
    ENVS=$(grep -v '^#' .env | xargs)
else
    ENVS=""
fi


if [ ! "$PROJECT_ID" ]; then
    # Setting the PROJECT_ID using gcloud config, if not specified as an environment variable.
    PROJECT_ID=$(gcloud config get-value project)
    if [ ! "$PROJECT_ID" ]; then
        echo "PROJECT_ID must be set using gcloud CLI, or must be specified as an environment variable." && exit 1
    fi
fi
# Setting the PROJECT_NUMBER from gcloud config based on the valid PROJECT_ID.
PROJECT_NUMBER=$(gcloud projects list --filter="$PROJECT_ID" --format='value("PROJECT_NUMBER")')

if [ ! "${PROJECT_NUMBER}" ]; then
    echo "Could not find PROJECT_NUMBER for PROJECT_ID: ${PROJECT_ID}, please validate the PROJECT_ID and try again." && exit 1
fi

# Adding PROJECT_ID and PROJECT_NUMBER to the list of environment variables.
ENVS=${ENVS}" PROJECT_ID=$PROJECT_ID PROJECT_NUMBER=${PROJECT_NUMBER}"

if [ ! "${SERVICE_NAME}" ]; then
    echo "SERVICE_NAME must be specified as an environment variable." && exit 1
fi

if [ ! "${GCP_REGION}" ]; then
    GCP_REGION="us-east1"
fi

if [ ! "${PORT}" ]; then
    PORT=8000
fi

tag=$CI_COMMIT_TAG

gcloud run deploy "${SERVICE_NAME}" \
    --image gcr.io/"$PROJECT_ID"/"${GCR_REPO}"/"$tag":"$tag" \
    --platform managed \
    --set-env-vars="${ENVS// /,}" \
    --port ${PORT} \
    --region ${GCP_REGION} \
    --allow-unauthenticated \
    --vpc-connector ${VPC_CONNECTOR} \
    --min-instances=1

gcloud run services update-traffic "${SERVICE_NAME}" --to-latest --region ${GCP_REGION}
