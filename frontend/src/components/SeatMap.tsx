import type { JoinedSection } from '../lib/seatMap'
import { formatMoney, formatSeatLabel } from '../lib/format'

const STATUS_COLOR: Record<string, string> = {
  available: '#4caf50',
  held: '#ff9800',
  booked: '#9e9e9e',
  unprovisioned: '#e0e0e0',
}

export interface SelectedSeatInfo {
  sectionName: string
  rowName: string
  seatLabel: string
  priceCents: number
}

interface SeatMapProps {
  sections: JoinedSection[]
  onSelectSeat: (ticketId: string, info: SelectedSeatInfo) => void
}

export function SeatMap({ sections, onSelectSeat }: SeatMapProps) {
  return (
    <div>
      {sections.map((section) => (
        <section key={section.name}>
          <h3>
            {section.name} — {formatMoney(section.priceCents)} base
          </h3>
          {section.rows.map((row) => (
            <div key={row.name} style={{ display: 'flex', gap: 4, alignItems: 'center', marginBottom: 4 }}>
              <span style={{ width: 24 }}>{row.name}</span>
              {row.seats.map((seat) => {
                const bookable = seat.status === 'available' && seat.ticketId !== null
                return (
                  <button
                    key={seat.label}
                    disabled={!bookable}
                    title={`${formatSeatLabel(section.name, row.name, seat.label)} — ${seat.status}`}
                    onClick={() =>
                      bookable &&
                      onSelectSeat(seat.ticketId!, {
                        sectionName: section.name,
                        rowName: row.name,
                        seatLabel: seat.label,
                        priceCents: seat.priceCents,
                      })
                    }
                    style={{
                      width: 28,
                      height: 28,
                      background: STATUS_COLOR[seat.status],
                      cursor: bookable ? 'pointer' : 'not-allowed',
                    }}
                  >
                    {seat.label}
                  </button>
                )
              })}
            </div>
          ))}
        </section>
      ))}
    </div>
  )
}
