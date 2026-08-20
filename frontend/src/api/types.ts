// Mirrors the backend Pydantic response schemas this app consumes.
// See services/{event,search,booking}-service/app/api/schemas.py.

export interface Venue {
  id: string
  name: string
  address: string
  capacity: number
}

export interface Performer {
  id: string
  name: string
  bio: string | null
}

export type EventStatus = 'draft' | 'published'

export interface EventDetail {
  id: string
  title: string
  description: string | null
  start_time: string
  end_time: string
  status: EventStatus
  organizer_id: string
  venue: Venue
  performers: Performer[]
}

export interface Seat {
  label: string
  x: number
  y: number
}

export interface SeatMapRow {
  name: string
  seats: Seat[]
}

export interface SeatMapSection {
  name: string
  rows: SeatMapRow[]
  price_cents: number
}

export interface SeatMap {
  event_id: string
  sections: SeatMapSection[]
}

export type TicketStatus = 'available' | 'held' | 'booked'

export interface TicketStatusEntry {
  ticket_id: string
  section: string
  row_name: string
  seat_label: string
  status: TicketStatus
  price_cents: number
}

export interface SearchResultItem {
  event_id: string
  title: string
  description: string | null
  start_time: string
  end_time: string
  venue_name: string
  performer_names: string[]
}

export interface SearchResponse {
  items: SearchResultItem[]
  total: number
  limit: number
  offset: number
}

export type BookingStatus = 'pending' | 'confirmed' | 'cancelled' | 'expired'

export interface BookingResponse {
  id: string
  user_subject: string
  event_id: string
  ticket_id: string
  status: BookingStatus
  created_at: string
}

export interface BookingPayResponse {
  payment_id: string
  status: string
  amount_cents: number
  currency: string
}
