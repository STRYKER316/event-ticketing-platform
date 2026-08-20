import type { AuthProviderProps } from 'react-oidc-context'

// Same "fail loudly at startup" reasoning as api/client.ts's
// SERVICE_BASE_URLS check — an unset issuer would otherwise resolve OIDC
// discovery to "undefined/.well-known/openid-configuration" and break
// login with no useful error.
const issuer = import.meta.env.VITE_KEYCLOAK_ISSUER
if (!issuer) throw new Error('Missing VITE_KEYCLOAK_ISSUER')

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
