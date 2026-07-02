import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The backend's CORS default allows http://localhost:5173, so the port is pinned
// (strictPort) rather than letting Vite silently drift to 5174 when 5173 is busy.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
})
