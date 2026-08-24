const SERVICE_BASE_URLS = {
  event: import.meta.env.VITE_EVENT_SERVICE_URL,
  search: import.meta.env.VITE_SEARCH_SERVICE_URL,
  booking: import.meta.env.VITE_BOOKING_SERVICE_URL,
} as const

// Fail loudly rather than resolving fetch URLs to "undefined/...". Catches
// a missing key in the standalone npm-run-dev .env file -- the Docker build
// (frontend/Dockerfile's ENV VITE_X=${VITE_X}) always yields "" for an
// unset ARG, never undefined, and "" is the deliberate relative-URL value.
for (const [service, url] of Object.entries(SERVICE_BASE_URLS)) {
  if (url === undefined) throw new Error(`Missing VITE_${service.toUpperCase()}_SERVICE_URL`)
}

type Service = keyof typeof SERVICE_BASE_URLS

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

// Thin fetch wrapper: attaches the current bearer token when present,
// resolves the path against the right backend service's base URL, and
// turns a non-2xx response into an ApiError carrying the backend's own
// message (validation/ownership/conflict text) rather than a generic one.
export async function apiFetch<T>(
  service: Service,
  path: string,
  options: { method?: string; body?: unknown; token?: string } = {},
): Promise<T> {
  const { method = 'GET', body, token } = options
  const headers: Record<string, string> = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (token) headers.Authorization = `Bearer ${token}`

  const response = await fetch(`${SERVICE_BASE_URLS[service]}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

  if (!response.ok) {
    const detail = await response.json().catch(() => null)
    const message =
      (typeof detail?.detail === 'string' && detail.detail) ||
      (Array.isArray(detail?.detail) && detail.detail.map((e: { msg: string }) => e.msg).join('; ')) ||
      response.statusText
    throw new ApiError(response.status, message)
  }

  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

// A rejection reaching a .catch isn't guaranteed to be the ApiError apiFetch
// throws — a network-level failure (offline, CORS, DNS) surfaces as a plain
// fetch TypeError with no .status — so callers that want to branch on status
// must narrow with instanceof rather than assuming the type.
export function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : 'Something went wrong.'
}
