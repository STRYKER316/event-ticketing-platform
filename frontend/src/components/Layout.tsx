import { Link, Outlet } from 'react-router-dom'
import { useAuth } from 'react-oidc-context'
import { useRoles } from '../auth/roles'

export function Layout() {
  const auth = useAuth()
  const roles = useRoles()
  const isOrganizer = roles.includes('organizer')

  return (
    <div>
      <header>
        <nav style={{ display: 'flex', gap: 12 }}>
          <Link to="/search">Browse events</Link>
          {isOrganizer && <Link to="/organizer">Organizer</Link>}
        </nav>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
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
