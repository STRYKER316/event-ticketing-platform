import type { BookingPayResponse, BookingResponse } from '../api/types'

export type CheckoutState =
  | { status: 'idle' }
  | { status: 'holding' }
  | { status: 'held'; booking: BookingResponse }
  | { status: 'paying'; booking: BookingResponse }
  | { status: 'paid'; payment: BookingPayResponse }
  | { status: 'hold_failed'; error: string }
  | { status: 'payment_failed'; booking: BookingResponse; error: string }

export type CheckoutAction =
  | { type: 'HOLD_REQUESTED' }
  | { type: 'HOLD_SUCCEEDED'; booking: BookingResponse }
  | { type: 'HOLD_FAILED'; error: string }
  | { type: 'PAY_REQUESTED' }
  | { type: 'PAY_SUCCEEDED'; payment: BookingPayResponse }
  | { type: 'PAY_FAILED'; error: string }

export const initialCheckoutState: CheckoutState = { status: 'idle' }

// Pure so the state transitions (in particular: a failed hold has no
// booking to retry payment against, and a failed payment keeps the
// existing PENDING booking rather than discarding it) are testable without
// mounting the checkout screen's React component.
export function checkoutReducer(state: CheckoutState, action: CheckoutAction): CheckoutState {
  switch (action.type) {
    case 'HOLD_REQUESTED':
      return { status: 'holding' }
    case 'HOLD_SUCCEEDED':
      return { status: 'held', booking: action.booking }
    case 'HOLD_FAILED':
      return { status: 'hold_failed', error: action.error }
    case 'PAY_REQUESTED':
      if (state.status !== 'held' && state.status !== 'payment_failed') return state
      return { status: 'paying', booking: state.booking }
    case 'PAY_SUCCEEDED':
      return { status: 'paid', payment: action.payment }
    case 'PAY_FAILED':
      if (state.status !== 'paying') return state
      return { status: 'payment_failed', booking: state.booking, error: action.error }
    default:
      return state
  }
}
