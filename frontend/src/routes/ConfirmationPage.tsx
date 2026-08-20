import { Link, useLocation } from 'react-router-dom'
import type { BookingPayResponse } from '../api/types'
import type { SelectedSeatInfo } from '../components/SeatMap'
import { formatMoney } from '../lib/format'

// Renders only from the state a successful CheckoutPage payment hands off —
// there is no GET /bookings/{id} route (a documented, deliberate limitation,
// decisions-log §26), so a hard refresh loses this detail. The underlying
// booking is unaffected; it is already CONFIRMED server-side.
export function ConfirmationPage() {
  const location = useLocation()
  const state = location.state as { payment: BookingPayResponse; seatInfo: SelectedSeatInfo | null } | null

  if (!state) {
    return (
      <div>
        <p>No confirmation to show — this page only renders right after a payment completes.</p>
        <Link to="/search">Back to browsing</Link>
      </div>
    )
  }

  const { payment, seatInfo } = state
  return (
    <div>
      <h1>Booking confirmed</h1>
      {seatInfo && (
        <p>
          {seatInfo.sectionName} {seatInfo.rowName}
          {seatInfo.seatLabel}
        </p>
      )}
      <p>
        Paid {formatMoney(payment.amount_cents)} {payment.currency.toUpperCase()} — status: {payment.status}
      </p>
      <p>Payment ID: {payment.payment_id}</p>
      <Link to="/search">Back to browsing</Link>
    </div>
  )
}
