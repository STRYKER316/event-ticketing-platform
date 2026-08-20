import { useEffect, useReducer } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { useAuth } from 'react-oidc-context'
import { createBooking, payBooking } from '../api/booking'
import { ApiError } from '../api/client'
import { checkoutReducer, initialCheckoutState } from '../lib/checkout'
import { formatMoney } from '../lib/format'
import type { SelectedSeatInfo } from '../components/SeatMap'
import { ErrorText } from '../components/ErrorText'

export function CheckoutPage() {
  const { ticketId } = useParams<{ ticketId: string }>()
  const location = useLocation()
  const navigate = useNavigate()
  const auth = useAuth()
  const [state, dispatch] = useReducer(checkoutReducer, initialCheckoutState)
  const seatInfo = location.state as (SelectedSeatInfo & { eventId: string }) | null

  const token = auth.user?.access_token

  useEffect(() => {
    if (!ticketId || !token) return
    dispatch({ type: 'HOLD_REQUESTED' })
    createBooking(ticketId, token)
      .then((booking) => dispatch({ type: 'HOLD_SUCCEEDED', booking }))
      .catch((err: ApiError) =>
        dispatch({
          type: 'HOLD_FAILED',
          error: err.status === 409 ? 'This seat was just taken by someone else.' : err.message,
        }),
      )
    // Runs once per mount — re-holding on every render would double-acquire.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticketId])

  const handlePay = () => {
    if (state.status !== 'held' && state.status !== 'payment_failed') return
    if (!token) return
    dispatch({ type: 'PAY_REQUESTED' })
    payBooking(state.booking.id, token)
      .then((payment) => {
        dispatch({ type: 'PAY_SUCCEEDED', payment })
        navigate('/confirmation', { state: { payment, seatInfo } })
      })
      .catch((err: ApiError) => dispatch({ type: 'PAY_FAILED', error: err.message }))
  }

  if (state.status === 'idle' || state.status === 'holding') return <p>Holding your seat...</p>

  if (state.status === 'hold_failed') {
    return (
      <div>
        <ErrorText message={state.error} />
        <button onClick={() => navigate(-1)}>Back to seat map</button>
      </div>
    )
  }

  return (
    <div>
      <h1>Checkout</h1>
      {seatInfo && (
        <p>
          {seatInfo.sectionName} {seatInfo.rowName}
          {seatInfo.seatLabel} — {formatMoney(seatInfo.priceCents)}
        </p>
      )}
      {state.status === 'payment_failed' && <ErrorText message={`Payment failed: ${state.error}`} />}
      <button onClick={handlePay} disabled={state.status === 'paying'}>
        {state.status === 'paying' ? 'Paying...' : 'Pay'}
      </button>
    </div>
  )
}
