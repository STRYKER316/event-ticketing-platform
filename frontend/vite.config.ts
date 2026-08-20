/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// base: '/app/' matches the Traefik PathPrefix('/app') mount (decisions-log
// §23 amendment) — asset URLs in the built index.html resolve correctly
// whether served through the gateway or previewed standalone.
export default defineConfig({
  base: '/app/',
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
})
