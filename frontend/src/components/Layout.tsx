import type { ReactNode } from 'react'
import { Link, Outlet } from 'react-router-dom'
import { useAuth } from 'react-oidc-context'
import { accessTokenRoles } from '../auth/roles'

export function Layout({ children }: { children?: ReactNode }) {
  const auth = useAuth()
  const isOrganizer = accessTokenRoles(auth.user?.access_token).includes('organizer')

  return (
    <div>
      <header>
        <nav>
          <Link to="/search">Browse events</Link>
          {isOrganizer && <Link to="/organizer">Organizer</Link>}
        </nav>
        <div>
          {auth.isAuthenticated ? (
            <>
              <span>{auth.user?.profile.preferred_username}</span>
              <button onClick={() => auth.signoutRedirect()}>Log out</button>
            </>
          ) : (
            <button onClick={() => auth.signinRedirect()}>Log in / Register</button>
          )}
        </div>
      </header>
      <main>{children ?? <Outlet />}</main>
    </div>
  )
}
