import { useMemo } from 'react'
import { Link, Outlet } from 'react-router-dom'
import { useAuth } from 'react-oidc-context'
import { accessTokenRoles } from '../auth/roles'

export function Layout() {
  const auth = useAuth()
  const roles = useMemo(() => accessTokenRoles(auth.user?.access_token), [auth.user?.access_token])
  const isOrganizer = roles.includes('organizer')

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
      <main>
        <Outlet />
      </main>
    </div>
  )
}
