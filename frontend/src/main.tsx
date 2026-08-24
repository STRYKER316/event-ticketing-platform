// Side-effect only: installs a crypto.subtle.digest fallback before
// anything else runs, in case this page is loaded over plain HTTP on a
// real hostname (see the file for why) -- must be the first import so it
// runs before oidc-client-ts's PKCE code_challenge ever gets a chance to
// need it.
import './auth/subtleCryptoPolyfill.ts'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AuthProvider } from 'react-oidc-context'
import './index.css'
import App from './App.tsx'
import { oidcConfig } from './auth/oidcConfig.ts'

// Default staleTime avoids refetching unchanged data on every remount/window
// refocus; queries that need fresher data (e.g. live ticket status) opt into
// their own refetchInterval/staleTime instead.
const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 30_000 } } })

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AuthProvider {...oidcConfig}>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter basename="/app">
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </AuthProvider>
  </StrictMode>,
)
