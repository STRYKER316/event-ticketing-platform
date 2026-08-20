import { apiFetch } from './client'
import type { BookingPayResponse, BookingResponse, TicketStatusEntry } from './types'

export function getTicketsForEvent(eventId: string): Promise<TicketStatusEntry[]> {
  return apiFetch<TicketStatusEntry[]>('booking', `/bookings/events/${eventId}/tickets`)
}

export function createBooking(ticketId: string, token: string): Promise<BookingResponse> {
  return apiFetch<BookingResponse>('booking', '/bookings', {
    method: 'POST',
    body: { ticket_id: ticketId },
    token,
  })
}

export function payBooking(bookingId: string, token: string): Promise<BookingPayResponse> {
  return apiFetch<BookingPayResponse>('booking', `/bookings/${bookingId}/pay`, {
    method: 'POST',
    token,
  })
}
