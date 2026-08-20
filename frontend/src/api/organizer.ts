import { apiFetch } from './client'
import type { EventDetail, SeatMap, Venue } from './types'

export interface VenueCreate {
  name: string
  address: string
  capacity: number
}

export function createVenue(payload: VenueCreate, token: string): Promise<Venue> {
  return apiFetch<Venue>('event', '/venues', { method: 'POST', body: payload, token })
}

export interface EventCreate {
  title: string
  description: string
  start_time: string
  end_time: string
  venue_id: string
}

export function createEvent(payload: EventCreate, token: string): Promise<EventDetail> {
  // performer_ids omitted (defaults to []) — event-service has no
  // performer-create/list route, see phase-7-kickoff.md's P7.T5 note.
  return apiFetch<EventDetail>('event', '/events', { method: 'POST', body: payload, token })
}

export interface SeatMapSectionInput {
  name: string
  price_cents: number
  rows: { name: string; seats: { label: string; x: number; y: number }[] }[]
}

export function upsertSeatMap(eventId: string, sections: SeatMapSectionInput[], token: string): Promise<SeatMap> {
  return apiFetch<SeatMap>('event', `/events/${eventId}/seat-map`, {
    method: 'PUT',
    body: { sections },
    token,
  })
}

export function publishEvent(eventId: string, token: string): Promise<EventDetail> {
  return apiFetch<EventDetail>('event', `/events/${eventId}/publish`, { method: 'POST', token })
}
