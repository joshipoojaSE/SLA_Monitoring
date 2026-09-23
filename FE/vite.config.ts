import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // A fixed port, so it always matches CORS_ORIGINS on the API. strictPort
  // fails loudly instead of drifting to a port the API does not allow.
  server: { port: 5174, strictPort: true },
})
