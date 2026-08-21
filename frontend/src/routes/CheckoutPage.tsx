import { useEffect, useReducer, useRef } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { useAuth } from 'react-oidc-context'
import { createBooking, payBooking } from '../api/booking'
import { ApiError, errorMessage } from '../api/client'
import { checkoutReducer, initialCheckoutState } from '../lib/checkout'
import { formatMoney, formatSeatLabel } from '../lib/format'
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

  // Guards against React StrictMode's dev-only double-invoke of this effect:
  // without it, the second invocation re-holds the same ticket, loses the
  // real API's race to the first hold, and its 409 overwrites the correct
  // HOLD_SUCCEEDED with a false "someone else took this seat" error.
  const heldTicketRef = useRef<string | null>(null)

  useEffect(() => {
    if (!ticketId || !token) return
    if (heldTicketRef.current === ticketId) return
    heldTicketRef.current = ticketId
    dispatch({ type: 'HOLD_REQUESTED' })
    createBooking(ticketId, token)
      .then((booking) => dispatch({ type: 'HOLD_SUCCEEDED', booking }))
      .catch((err: unknown) =>
        dispatch({
          type: 'HOLD_FAILED',
          error: err instanceof ApiError && err.status === 409 ? 'This seat was just taken by someone else.' : errorMessage(err),
        }),
      )
  }, [ticketId, token])

  const handlePay = () => {
    if (state.status !== 'held' && state.status !== 'payment_failed') return
    if (!token) return
    dispatch({ type: 'PAY_REQUESTED' })
    payBooking(state.booking.id, token)
      .then((payment) => {
        dispatch({ type: 'PAY_SUCCEEDED', payment })
        navigate('/confirmation', { state: { payment, seatInfo } })
      })
      .catch((err: unknown) => dispatch({ type: 'PAY_FAILED', error: errorMessage(err) }))
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
          {formatSeatLabel(seatInfo.sectionName, seatInfo.rowName, seatInfo.seatLabel)} — {formatMoney(seatInfo.priceCents)}
        </p>
      )}
      {state.status === 'payment_failed' && <ErrorText message={`Payment failed: ${state.error}`} />}
      <button onClick={handlePay} disabled={state.status === 'paying'}>
        {state.status === 'paying' ? 'Paying...' : 'Pay'}
      </button>
    </div>
  )
}
