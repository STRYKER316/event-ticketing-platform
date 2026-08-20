import { describe, expect, it } from 'vitest'
import { checkoutReducer, initialCheckoutState } from './checkout'
import type { BookingResponse } from '../api/types'

const booking: BookingResponse = {
  id: 'b1',
  user_subject: 'u1',
  event_id: 'e1',
  ticket_id: 't1',
  status: 'pending',
  created_at: '2026-01-01T00:00:00Z',
}

describe('checkoutReducer', () => {
  it('walks the happy path: idle -> holding -> held -> paying -> paid', () => {
    let state = checkoutReducer(initialCheckoutState, { type: 'HOLD_REQUESTED' })
    expect(state.status).toBe('holding')

    state = checkoutReducer(state, { type: 'HOLD_SUCCEEDED', booking })
    expect(state).toEqual({ status: 'held', booking })

    state = checkoutReducer(state, { type: 'PAY_REQUESTED' })
    expect(state).toEqual({ status: 'paying', booking })

    const payment = { payment_id: 'p1', status: 'succeeded', amount_cents: 5000, currency: 'usd' }
    state = checkoutReducer(state, { type: 'PAY_SUCCEEDED', payment })
    expect(state).toEqual({ status: 'paid', payment })
  })

  it('a failed hold has no booking to retry payment against — PAY_REQUESTED is a no-op from hold_failed', () => {
    const failedHold = checkoutReducer(initialCheckoutState, { type: 'HOLD_FAILED', error: 'seat already taken' })
    expect(failedHold).toEqual({ status: 'hold_failed', error: 'seat already taken' })

    const afterPayAttempt = checkoutReducer(failedHold, { type: 'PAY_REQUESTED' })
    expect(afterPayAttempt).toBe(failedHold)
  })

  it('a failed payment keeps the existing PENDING booking so retry is possible', () => {
    const held = checkoutReducer(initialCheckoutState, { type: 'HOLD_SUCCEEDED', booking })
    const paying = checkoutReducer(held, { type: 'PAY_REQUESTED' })
    const failed = checkoutReducer(paying, { type: 'PAY_FAILED', error: 'card declined' })
    expect(failed).toEqual({ status: 'payment_failed', booking, error: 'card declined' })

    const retried = checkoutReducer(failed, { type: 'PAY_REQUESTED' })
    expect(retried).toEqual({ status: 'paying', booking })
  })

  it('PAY_FAILED is a no-op unless currently paying', () => {
    const idle = checkoutReducer(initialCheckoutState, { type: 'PAY_FAILED', error: 'unexpected' })
    expect(idle).toBe(initialCheckoutState)
  })
})
