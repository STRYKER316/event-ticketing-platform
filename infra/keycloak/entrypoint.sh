#!/bin/sh
# Renders the realm import file from its template before Keycloak's own
# --import-realm startup step runs, substituting the actual public origin
# (APP_ORIGIN, set per-environment -- see .env.example) for the
# __APP_ORIGIN__ placeholder in realm-export.json.template. This is what
# lets the same committed template drive Keycloak's redirectUris/webOrigins
# correctly on both localhost and the EB deployment, with no rebuild.
set -eu

: "${APP_ORIGIN:?APP_ORIGIN must be set}"

mkdir -p /opt/keycloak/data/import
sed "s|__APP_ORIGIN__|${APP_ORIGIN}|g" \
  /tmp/realm-export.json.template \
  > /opt/keycloak/data/import/realm-export.json

exec /opt/keycloak/bin/kc.sh "$@"
