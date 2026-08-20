import { Navigate, Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { ProtectedRoute } from './components/ProtectedRoute'
import { SearchPage } from './routes/SearchPage'
import { EventDetailPage } from './routes/EventDetailPage'
import { CheckoutPage } from './routes/CheckoutPage'
import { ConfirmationPage } from './routes/ConfirmationPage'
import { OrganizerPage } from './routes/OrganizerPage'

function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Navigate to="/search" replace />} />
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
