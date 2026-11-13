import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// The dev server proxies the backend so the browser talks to one origin.
const backend = process.env.BACKEND_URL || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': backend,
      '/ws': { target: backend.replace(/^http/, 'ws'), ws: true },
    },
  },
})
