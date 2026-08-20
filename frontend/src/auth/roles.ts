// Client-side-only role check for hiding UI, not a security boundary —
// real enforcement is server-side (require_role, unchanged by this phase).
// The access token (not the ID token profile) carries realm_access.roles.
export function accessTokenRoles(accessToken: string | undefined): string[] {
  if (!accessToken) return []
  const payload = accessToken.split('.')[1]
  if (!payload) return []
  try {
    const decoded = JSON.parse(atob(payload.replace(/-/g, '+').replace(/_/g, '/')))
    return Array.isArray(decoded.realm_access?.roles) ? decoded.realm_access.roles : []
  } catch {
    return []
  }
}
