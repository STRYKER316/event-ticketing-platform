import { apiFetch } from './client'
import type { EventDetail, SeatMap } from './types'

export function getEvent(eventId: string): Promise<EventDetail> {
  return apiFetch<EventDetail>('event', `/events/${eventId}`)
}

export function getSeatMap(eventId: string): Promise<SeatMap> {
  return apiFetch<SeatMap>('event', `/events/${eventId}/seat-map`)
}
