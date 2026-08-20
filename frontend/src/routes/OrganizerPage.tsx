import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { useAuth } from 'react-oidc-context'
import { createEvent, createVenue, publishEvent, upsertSeatMap } from '../api/organizer'
import type { SeatMapSectionInput } from '../api/organizer'
import { ErrorText } from '../components/ErrorText'

type Step = 'venue' | 'event' | 'seat-map' | 'publish' | 'done'

export function OrganizerPage() {
  const auth = useAuth()
  const token = auth.user!.access_token
  const [venueId, setVenueId] = useState<string | null>(null)
  const [eventId, setEventId] = useState<string | null>(null)
  const [sections, setSections] = useState<SeatMapSectionInput[]>([])

  const venueMutation = useMutation({
    mutationFn: (payload: { name: string; address: string; capacity: number }) => createVenue(payload, token),
    onSuccess: (venue) => setVenueId(venue.id),
  })

  const eventMutation = useMutation({
    mutationFn: (payload: { title: string; description: string; start_time: string; end_time: string }) =>
      createEvent({ ...payload, venue_id: venueId! }, token),
    onSuccess: (event) => setEventId(event.id),
  })

  const seatMapMutation = useMutation({
    mutationFn: () => upsertSeatMap(eventId!, sections, token),
  })

  const publishMutation = useMutation({
    mutationFn: () => publishEvent(eventId!, token),
  })

  // Derived from the state each step's completion actually produces, rather
  // than tracked separately and kept in sync by hand in each onSuccess.
  const step: Step = !venueId
    ? 'venue'
    : !eventId
      ? 'event'
      : !seatMapMutation.isSuccess
        ? 'seat-map'
        : !publishMutation.isSuccess
          ? 'publish'
          : 'done'

  const addSection = () => {
    setSections((prev) => [...prev, { name: '', price_cents: 0, rows: [] }])
  }

  const updateSection = (index: number, patch: Partial<SeatMapSectionInput>) => {
    setSections((prev) => prev.map((s, i) => (i === index ? { ...s, ...patch } : s)))
  }

  const addRow = (sectionIndex: number, rowName: string, seatLabelsCsv: string) => {
    const labels = seatLabelsCsv
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
    const currentRows = sections[sectionIndex].rows
    updateSection(sectionIndex, {
      rows: [
        ...currentRows,
        { name: rowName, seats: labels.map((label, idx) => ({ label, x: idx + 1, y: currentRows.length + 1 })) },
      ],
    })
  }

  return (
    <div>
      <h1>Create an event</h1>

      {step === 'venue' && (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            const form = new FormData(e.currentTarget)
            venueMutation.mutate({
              name: String(form.get('name')),
              address: String(form.get('address')),
              capacity: Number(form.get('capacity')),
            })
          }}
        >
          <h2>1. Venue</h2>
          <input name="name" placeholder="Venue name" required />
          <input name="address" placeholder="Address" required />
          <input name="capacity" type="number" min={1} placeholder="Capacity" required />
          <button type="submit" disabled={venueMutation.isPending}>
            Next
          </button>
          {venueMutation.error && <ErrorText message={venueMutation.error.message} />}
        </form>
      )}

      {step === 'event' && (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            const form = new FormData(e.currentTarget)
            eventMutation.mutate({
              title: String(form.get('title')),
              description: String(form.get('description') ?? ''),
              start_time: new Date(String(form.get('start_time'))).toISOString(),
              end_time: new Date(String(form.get('end_time'))).toISOString(),
            })
          }}
        >
          <h2>2. Event</h2>
          <input name="title" placeholder="Title" required />
          <textarea name="description" placeholder="Description" />
          <label>
            Start <input name="start_time" type="datetime-local" required />
          </label>
          <label>
            End <input name="end_time" type="datetime-local" required />
          </label>
          <button type="submit" disabled={eventMutation.isPending}>
            Next
          </button>
          {eventMutation.error && <ErrorText message={eventMutation.error.message} />}
        </form>
      )}

      {step === 'seat-map' && (
        <div>
          <h2>3. Seat map</h2>
          {sections.map((section, sectionIndex) => (
            <fieldset key={sectionIndex}>
              <input
                placeholder="Section name"
                value={section.name}
                onChange={(e) => updateSection(sectionIndex, { name: e.target.value })}
              />
              <input
                type="number"
                placeholder="Price (cents)"
                value={section.price_cents || ''}
                onChange={(e) => updateSection(sectionIndex, { price_cents: Number(e.target.value) })}
              />
              <ul>
                {section.rows.map((row) => (
                  <li key={row.name}>
                    Row {row.name}: {row.seats.map((s) => s.label).join(', ')}
                  </li>
                ))}
              </ul>
              <form
                onSubmit={(e) => {
                  e.preventDefault()
                  const form = new FormData(e.currentTarget)
                  addRow(sectionIndex, String(form.get('rowName')), String(form.get('seatLabels')))
                  e.currentTarget.reset()
                }}
              >
                <input name="rowName" placeholder="Row name" required />
                <input name="seatLabels" placeholder="Seat labels, comma-separated (e.g. 1,2,3)" required />
                <button type="submit">Add row</button>
              </form>
            </fieldset>
          ))}
          <button type="button" onClick={addSection}>
            Add section
          </button>
          <div>
            <button
              type="button"
              disabled={sections.length === 0 || seatMapMutation.isPending}
              onClick={() => seatMapMutation.mutate()}
            >
              Save seat map
            </button>
            {seatMapMutation.error && <ErrorText message={seatMapMutation.error.message} />}
          </div>
        </div>
      )}

      {step === 'publish' && (
        <div>
          <h2>4. Publish</h2>
          <button disabled={publishMutation.isPending} onClick={() => publishMutation.mutate()}>
            Publish event
          </button>
          {publishMutation.error && <ErrorText message={publishMutation.error.message} />}
        </div>
      )}

      {step === 'done' && eventId && (
        <div>
          <p>Event published.</p>
          <Link to={`/events/${eventId}`}>View event</Link>
        </div>
      )}
    </div>
  )
}
