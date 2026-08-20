import type { SeatMap, TicketStatusEntry, TicketStatus } from '../api/types'

export interface JoinedSeat {
  label: string
  x: number
  y: number
  ticketId: string | null
  status: TicketStatus | 'unprovisioned'
  priceCents: number
}

export interface JoinedRow {
  name: string
  seats: JoinedSeat[]
}

export interface JoinedSection {
  name: string
  priceCents: number
  rows: JoinedRow[]
}

// JSON-encoded, not a plain join — organizer-typed section/row names are free
// text (OrganizerPage's form has no character restriction), so a joining
// separator that could itself appear in one of the three parts (e.g. a space)
// risks two distinct (section, row, label) triples colliding on the same key.
// JSON.stringify escapes each part, so this is collision-safe regardless of
// what an organizer types.
function seatKey(section: string, row: string, label: string): string {
  return JSON.stringify([section, row, label])
}

// Composes Event Service's static layout with Booking Service's live
// per-seat status (decisions-log §23 amendment), keyed on
// (section, row_name, seat_label). A layout seat with no matching ticket
// row is 'unprovisioned' rather than treated as available — provisioning
// happens asynchronously off the event-publish Kafka message (§7), so a
// just-published event can briefly have layout without tickets yet; that
// state must render as not-bookable, not as a false "available" seat.
export function joinSeatMapWithStatus(seatMap: SeatMap, tickets: TicketStatusEntry[]): JoinedSection[] {
  const byKey = new Map(tickets.map((t) => [seatKey(t.section, t.row_name, t.seat_label), t]))

  return seatMap.sections.map((section) => ({
    name: section.name,
    priceCents: section.price_cents,
    rows: section.rows.map((row) => ({
      name: row.name,
      seats: row.seats.map((seat) => {
        const ticket = byKey.get(seatKey(section.name, row.name, seat.label))
        return {
          label: seat.label,
          x: seat.x,
          y: seat.y,
          ticketId: ticket?.ticket_id ?? null,
          status: ticket?.status ?? 'unprovisioned',
          priceCents: ticket?.price_cents ?? section.price_cents,
        }
      }),
    })),
  }))
}
