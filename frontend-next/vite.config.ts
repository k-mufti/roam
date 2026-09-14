import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // 5173 belongs to `frontend/` via `make dev`; this prototype runs alongside it.
  server: { port: Number(process.env.PORT) || 5174 },
})
