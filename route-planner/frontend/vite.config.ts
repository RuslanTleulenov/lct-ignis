import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Прокси на бэкенд, чтобы фронт ходил по относительным путям (/api, /ws)
// и не зависел от того, на каком хосте поднят сервис.
export default defineConfig({
  plugins: [react()],
  server: {
    // 5173 занят фронтендом соседнего проекта в этом же репозитории
    port: 5174,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
})
