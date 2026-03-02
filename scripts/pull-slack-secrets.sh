#!/bin/bash
# Pulls Slack-related secrets from AWS Secrets Manager and appends to mission-control.env.
# Run AFTER pull-secrets.sh and BEFORE starting containers.
set -euo pipefail
REGION="us-east-2"
SECRETS_DIR="${HOME}/infrastructure/secrets"
ENV_FILE="${SECRETS_DIR}/mission-control.env"

echo "Pulling Slack secrets from AWS Secrets Manager (${REGION})..."

SLACK_CLIENT_ID=$(aws secretsmanager get-secret-value \
  --secret-id "/openclaw/mission-control/slack-client-id" \
  --region "$REGION" --query 'SecretString' --output text 2>/dev/null || echo "")

SLACK_CLIENT_SECRET=$(aws secretsmanager get-secret-value \
  --secret-id "/openclaw/mission-control/slack-client-secret" \
  --region "$REGION" --query 'SecretString' --output text 2>/dev/null || echo "")

SLACK_APP_TOKEN=$(aws secretsmanager get-secret-value \
  --secret-id "/openclaw/mission-control/slack-app-token" \
  --region "$REGION" --query 'SecretString' --output text 2>/dev/null || echo "")

SLACK_SIGNING_SECRET=$(aws secretsmanager get-secret-value \
  --secret-id "/openclaw/mission-control/slack-signing-secret" \
  --region "$REGION" --query 'SecretString' --output text 2>/dev/null || echo "")

ENCRYPTION_KEY=$(aws secretsmanager get-secret-value \
  --secret-id "/openclaw/mission-control/encryption-key" \
  --region "$REGION" --query 'SecretString' --output text 2>/dev/null || echo "")

if [ -z "$SLACK_CLIENT_ID" ]; then
  echo "  ⚠️  Slack secrets not found — Slack integration will be disabled."
  exit 0
fi

# Append Slack secrets to the mission-control .env file
cat >> "$ENV_FILE" << EOF

# Slack OAuth Integration
SLACK_CLIENT_ID=${SLACK_CLIENT_ID}
SLACK_CLIENT_SECRET=${SLACK_CLIENT_SECRET}
SLACK_APP_TOKEN=${SLACK_APP_TOKEN}
SLACK_SIGNING_SECRET=${SLACK_SIGNING_SECRET}
ENCRYPTION_KEY=${ENCRYPTION_KEY}
SLACK_OAUTH_REDIRECT_URI=https://helm-api.blueorangedigital.co/api/v1/slack/oauth/callback
EOF
chmod 600 "$ENV_FILE"

echo "  ✅ Slack secrets appended to ${ENV_FILE}"
