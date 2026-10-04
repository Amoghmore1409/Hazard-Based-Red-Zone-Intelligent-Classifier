import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

const API = process.env.API_URL ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  optimizeDeps: { exclude: ['maplibre-gl'] },  // keep its worker module URL resolvable
  server: {
    host: true,
    proxy: { '/api': API, '/uploads': API },
  },
})
