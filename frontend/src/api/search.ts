import { apiFetch } from './client'
import type { SearchResponse } from './types'

export interface SearchParams {
  q: string
  limit: number
  offset: number
}

export function searchEvents({ q, limit, offset }: SearchParams): Promise<SearchResponse> {
  const params = new URLSearchParams({
    q,
    limit: String(limit),
    offset: String(offset),
    // Relevance sort is meaningless on an empty query (search-service's
    // own default) — browsing with no query sorts by start_time instead.
    sort_field: q ? 'relevance' : 'start_time',
    sort_order: q ? 'desc' : 'asc',
  })
  return apiFetch<SearchResponse>('search', `/search?${params.toString()}`)
}
