#!/bin/bash
# Prints a Keycloak access token via password grant, for manual API calls
# against services behind Traefik.
#
# Usage: ./get-token.sh [username] [password]
#   defaults to the seed user alice/changeme (see infra/keycloak/realm-export.json)
set -euo pipefail

USERNAME="${1:-alice}"
PASSWORD="${2:-changeme}"

[ -f .env ] && source .env

KEYCLOAK_HOST="${KEYCLOAK_HOST:-localhost}"
KEYCLOAK_PORT="${KEYCLOAK_PORT:-8081}"
KEYCLOAK_REALM="${KEYCLOAK_REALM:-ticketing}"

# The confidential, direct-access-grant-enabled client — not the public
# frontend client (PKCE-only, can't do password grant). See realm-export.json.
CLIENT_ID="ticketing-service"
CLIENT_SECRET="changeme"

RESPONSE=$(curl -s -X POST \
  "http://${KEYCLOAK_HOST}:${KEYCLOAK_PORT}/realms/${KEYCLOAK_REALM}/protocol/openid-connect/token" \
  -d grant_type=password \
  -d client_id="${CLIENT_ID}" \
  -d client_secret="${CLIENT_SECRET}" \
  -d username="${USERNAME}" \
  -d password="${PASSWORD}")

TOKEN=$(echo "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin).get('access_token',''))")

if [ -z "$TOKEN" ]; then
  echo "Failed to get token. Response: $RESPONSE" >&2
  exit 1
fi

echo "$TOKEN"
