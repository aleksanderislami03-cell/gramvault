import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// GramVault frontend dev/build config.
//
// - Dev server runs on Vite's default port (5173); `backend/gramvault/main.py`
//   already allows that origin via CORS for local development.
// - `/api` is proxied to the FastAPI dev server (default host/port from
//   `config.yaml`'s `server.*`, overridable via VITE_API_PROXY_TARGET) so the
//   frontend can always call same-origin `/api/...` paths in both dev and
//   production (where `main.py` mounts `frontend/dist/` and serves the API
//   from the same origin).
// - `build.outDir` is `dist` (Vite's default), matching the
//   `_FRONTEND_DIST_DIR` path `gramvault.main` expects.
const apiProxyTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: apiProxyTarget,
        changeOrigin: true,
      },
      '/media': {
        target: apiProxyTarget,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
