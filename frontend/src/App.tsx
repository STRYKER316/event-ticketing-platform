import { Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from 'react-oidc-context'
import { Layout } from './components/Layout'
import { ProtectedRoute } from './components/ProtectedRoute'
import { SearchPage } from './routes/SearchPage'
import { EventDetailPage } from './routes/EventDetailPage'
import { CheckoutPage } from './routes/CheckoutPage'
import { ConfirmationPage } from './routes/ConfirmationPage'
import { OrganizerPage } from './routes/OrganizerPage'

// "/" is also the OIDC redirect_uri, so Keycloak lands here with ?code=&state=
// after login. Navigating away immediately (the naive <Navigate> this used to
// be) wins the race against AuthProvider's own callback-processing effect and
// strips those params before it can exchange the code — login then silently
// never completes. Waiting out auth.isLoading first (same guard ProtectedRoute
// already uses) lets that effect finish before this route redirects.
function IndexRoute() {
  const auth = useAuth()
  if (auth.isLoading) return null
  return <Navigate to="/search" replace />
}

function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<IndexRoute />} />
        <Route path="/search" element={<SearchPage />} />
        <Route path="/events/:id" element={<EventDetailPage />} />
        <Route
          path="/checkout/:ticketId"
          element={
            <ProtectedRoute>
              <CheckoutPage />
            </ProtectedRoute>
          }
        />
        <Route path="/confirmation" element={<ConfirmationPage />} />
        <Route
          path="/organizer"
          element={
            <ProtectedRoute requireRole="organizer">
              <OrganizerPage />
            </ProtectedRoute>
          }
        />
      </Route>
    </Routes>
  )
}

export default App
