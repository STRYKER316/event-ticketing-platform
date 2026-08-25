/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// base: '/app/' matches the Traefik PathPrefix('/app') mount (decisions-log
// §23 amendment) — asset URLs in the built index.html resolve correctly
// whether served through the gateway or previewed standalone.

// The standalone dev server (:5173) is a different origin than Traefik
// (:80); no backend service sends Access-Control-Allow-Origin (deliberate —
// the Docker-served frontend is always same-origin), so proxy here instead
// of loosening backend CORS just for this one dev workflow.
const backendProxy = { target: 'http://localhost', changeOrigin: true }

export default defineConfig({
  base: '/app/',
  plugins: [react()],
  server: {
    proxy: {
      '/events': backendProxy,
      '/venues': backendProxy,
      '/search': backendProxy,
      '/bookings': backendProxy,
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
})
