import { useMemo } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useAuth } from 'react-oidc-context'
import { getEvent, getSeatMap } from '../api/events'
import { getTicketsForEvent } from '../api/booking'
import { joinSeatMapWithStatus } from '../lib/seatMap'
import { SeatMap } from '../components/SeatMap'
import type { SelectedSeatInfo } from '../components/SeatMap'
import { ErrorText } from '../components/ErrorText'

const POLL_INTERVAL_MS = 5000

export function EventDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const auth = useAuth()
  const eventId = id!

  const eventQuery = useQuery({ queryKey: ['event', eventId], queryFn: () => getEvent(eventId) })
  // Layout is static per event (§23) — fetched once, no polling.
  const seatMapQuery = useQuery({ queryKey: ['seat-map', eventId], queryFn: () => getSeatMap(eventId) })
  // Live status polls per §23's "polling, not push" decision.
  const ticketsQuery = useQuery({
    queryKey: ['tickets', eventId],
    queryFn: () => getTicketsForEvent(eventId),
    refetchInterval: POLL_INTERVAL_MS,
  })

  const seatMap = seatMapQuery.data
  const tickets = ticketsQuery.data
  // Hook must run every render regardless of the loading/error early-returns below.
  const sections = useMemo(() => (seatMap ? joinSeatMapWithStatus(seatMap, tickets ?? []) : []), [seatMap, tickets])

  if (eventQuery.isLoading || seatMapQuery.isLoading) return <p>Loading...</p>
  if (eventQuery.error) return <ErrorText message={`Failed to load event: ${eventQuery.error.message}`} />
  if (seatMapQuery.error) return <ErrorText message={`Failed to load seat map: ${seatMapQuery.error.message}`} />

  const event = eventQuery.data!

  const handleSelectSeat = (ticketId: string, info: SelectedSeatInfo) => {
    if (!auth.isAuthenticated) {
      auth.signinRedirect()
      return
    }
    navigate(`/checkout/${ticketId}`, { state: { eventId, ...info } })
  }

  return (
    <div>
      <h1>{event.title}</h1>
      <p>{event.description}</p>
      <p>
        {event.venue.name} — {new Date(event.start_time).toLocaleString()}
      </p>
      <SeatMap sections={sections} onSelectSeat={handleSelectSeat} />
    </div>
  )
}
