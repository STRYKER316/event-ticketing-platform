import type { ReactNode } from 'react'
import { useAuth } from 'react-oidc-context'
import { accessTokenRoles } from '../auth/roles'

interface ProtectedRouteProps {
  children: ReactNode
  requireRole?: string
}

export function ProtectedRoute({ children, requireRole }: ProtectedRouteProps) {
  const auth = useAuth()

  if (auth.isLoading) return <p>Loading...</p>

  if (!auth.isAuthenticated) {
    return (
      <div>
        <p>You need to log in to view this page.</p>
        <button onClick={() => auth.signinRedirect()}>Log in</button>
      </div>
    )
  }

  if (requireRole && !accessTokenRoles(auth.user?.access_token).includes(requireRole)) {
    return <p>You don't have access to this page.</p>
  }

  return <>{children}</>
}
