/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_KEYCLOAK_ISSUER: string
  readonly VITE_EVENT_SERVICE_URL: string
  readonly VITE_SEARCH_SERVICE_URL: string
  readonly VITE_BOOKING_SERVICE_URL: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
