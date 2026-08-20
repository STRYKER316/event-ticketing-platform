import { describe, expect, it } from 'vitest'
import { joinSeatMapWithStatus } from './seatMap'
import type { SeatMap, TicketStatusEntry } from '../api/types'

const seatMap: SeatMap = {
  event_id: 'e1',
  sections: [
    {
      name: 'Floor',
      price_cents: 5000,
      rows: [
        {
          name: 'A',
          seats: [
            { label: '1', x: 1, y: 1 },
            { label: '2', x: 2, y: 1 },
          ],
        },
      ],
    },
  ],
}

describe('joinSeatMapWithStatus', () => {
  it('attaches ticket_id/status/price to a seat with a matching ticket', () => {
    const tickets: TicketStatusEntry[] = [
      { ticket_id: 't1', section: 'Floor', row_name: 'A', seat_label: '1', status: 'available', price_cents: 5000 },
      { ticket_id: 't2', section: 'Floor', row_name: 'A', seat_label: '2', status: 'held', price_cents: 5000 },
    ]

    const [section] = joinSeatMapWithStatus(seatMap, tickets)
    const [row] = section.rows
    expect(row.seats[0]).toMatchObject({ label: '1', ticketId: 't1', status: 'available' })
    expect(row.seats[1]).toMatchObject({ label: '2', ticketId: 't2', status: 'held' })
  })

  it('marks a layout seat with no matching ticket as unprovisioned, not available', () => {
    const [section] = joinSeatMapWithStatus(seatMap, [])
    const [row] = section.rows
    expect(row.seats[0].status).toBe('unprovisioned')
    expect(row.seats[0].ticketId).toBeNull()
  })

  it('does not cross-match seats from a different section with the same row/label', () => {
    const twoSectionMap: SeatMap = {
      event_id: 'e1',
      sections: [
        { name: 'Floor', price_cents: 5000, rows: [{ name: 'A', seats: [{ label: '1', x: 1, y: 1 }] }] },
        { name: 'Balcony', price_cents: 3000, rows: [{ name: 'A', seats: [{ label: '1', x: 1, y: 1 }] }] },
      ],
    }
    const tickets: TicketStatusEntry[] = [
      { ticket_id: 't-floor', section: 'Floor', row_name: 'A', seat_label: '1', status: 'booked', price_cents: 5000 },
    ]

    const [floor, balcony] = joinSeatMapWithStatus(twoSectionMap, tickets)
    expect(floor.rows[0].seats[0].ticketId).toBe('t-floor')
    expect(balcony.rows[0].seats[0].ticketId).toBeNull()
  })
})
