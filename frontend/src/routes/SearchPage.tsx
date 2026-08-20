import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { searchEvents } from '../api/search'
import { ErrorText } from '../components/ErrorText'

const PAGE_SIZE = 20

export function SearchPage() {
  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [offset, setOffset] = useState(0)

  // Only re-query 300ms after the user stops typing.
  useEffect(() => {
    const handle = setTimeout(() => setDebouncedQuery(query), 300)
    return () => clearTimeout(handle)
  }, [query])

  const { data, isLoading, error } = useQuery({
    queryKey: ['search', debouncedQuery, offset],
    queryFn: () => searchEvents({ q: debouncedQuery, limit: PAGE_SIZE, offset }),
  })

  return (
    <div>
      <h1>Browse events</h1>
      <input
        type="text"
        placeholder="Search events..."
        value={query}
        onChange={(e) => {
          setQuery(e.target.value)
          setOffset(0)
        }}
      />

      {isLoading && <p>Loading...</p>}
      {error && <ErrorText message={`Failed to load events: ${error.message}`} />}

      <ul>
        {data?.items.map((item) => (
          <li key={item.event_id}>
            <Link to={`/events/${item.event_id}`}>
              <strong>{item.title}</strong> — {item.venue_name} — {new Date(item.start_time).toLocaleString()}
            </Link>
          </li>
        ))}
      </ul>

      {data && (
        <div>
          <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
            Previous
          </button>
          <span>
            {offset + 1}–{Math.min(offset + PAGE_SIZE, data.total)} of {data.total}
          </span>
          <button disabled={offset + PAGE_SIZE >= data.total} onClick={() => setOffset(offset + PAGE_SIZE)}>
            Next
          </button>
        </div>
      )}
    </div>
  )
}
