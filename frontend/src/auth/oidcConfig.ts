import type { AuthProviderProps } from 'react-oidc-context'

// Falsy check, not just undefined: an unset Dockerfile ARG passed through
// `ENV VITE_X=${VITE_X}` becomes "" here, not undefined, and unlike
// api/client.ts's SERVICE_BASE_URLS, "" is never a valid port/realm.
const keycloakPort = import.meta.env.VITE_KEYCLOAK_PORT
const keycloakRealm = import.meta.env.VITE_KEYCLOAK_REALM
if (!keycloakPort) throw new Error('Missing VITE_KEYCLOAK_PORT')
if (!keycloakRealm) throw new Error('Missing VITE_KEYCLOAK_REALM')

// Keycloak isn't proxied through Traefik, so unlike client.ts's relative
// URLs, only the host can vary by environment -- resolved from
// window.location instead of baked in, so one build works everywhere.
const issuer = `${window.location.protocol}//${window.location.hostname}:${keycloakPort}/realms/${keycloakRealm}`

// Authorization Code + PKCE against the realm's public `ticketing-frontend`
// client (standardFlowEnabled: true, directAccessGrantsEnabled: false —
// see infra/keycloak/realm-export.json.template). redirect_uri must match
// one of that client's registered redirectUris exactly.
export const oidcConfig: AuthProviderProps = {
  authority: issuer,
  client_id: 'ticketing-frontend',
  redirect_uri: `${window.location.origin}/app/`,
  post_logout_redirect_uri: `${window.location.origin}/app/`,
  onSigninCallback: () => {
    // Strips the ?code=&state= OIDC callback params back to a clean URL
    // after the redirect completes, instead of leaving them in the address
    // bar (react-oidc-context's own documented pattern for this).
    window.history.replaceState({}, document.title, window.location.pathname)
  },
}
