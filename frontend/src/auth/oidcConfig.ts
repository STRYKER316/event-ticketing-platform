import type { AuthProviderProps } from 'react-oidc-context'

// Authorization Code + PKCE against the realm's public `ticketing-frontend`
// client (standardFlowEnabled: true, directAccessGrantsEnabled: false —
// see infra/keycloak/realm-export.json). redirect_uri must match one of
// that client's registered redirectUris exactly.
export const oidcConfig: AuthProviderProps = {
  authority: import.meta.env.VITE_KEYCLOAK_ISSUER,
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
