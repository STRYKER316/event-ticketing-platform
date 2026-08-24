import type { AuthProviderProps } from 'react-oidc-context'

// Same "fail loudly at startup" reasoning as api/client.ts's
// SERVICE_BASE_URLS check — an unset port/realm would otherwise resolve
// OIDC discovery to a broken URL and break login with no useful error.
const keycloakPort = import.meta.env.VITE_KEYCLOAK_PORT
const keycloakRealm = import.meta.env.VITE_KEYCLOAK_REALM
if (keycloakPort === undefined) throw new Error('Missing VITE_KEYCLOAK_PORT')
if (keycloakRealm === undefined) throw new Error('Missing VITE_KEYCLOAK_REALM')

// Unlike the three service URLs in api/client.ts, Keycloak isn't proxied
// through Traefik (infra/docker-compose.yml's keycloak service has no
// traefik.* labels — it publishes its own host port directly), so it can't
// be resolved as a same-origin relative path. The port and realm are the
// same in every environment; only the host differs, so it's read from
// wherever the page is actually running rather than baked in at build
// time — this is what makes the same built image work unchanged against
// localhost and the EB public address, with no rebuild.
const issuer = `${window.location.protocol}//${window.location.hostname}:${keycloakPort}/realms/${keycloakRealm}`

// Authorization Code + PKCE against the realm's public `ticketing-frontend`
// client (standardFlowEnabled: true, directAccessGrantsEnabled: false —
// see infra/keycloak/realm-export.json). redirect_uri must match one of
// that client's registered redirectUris exactly.
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
