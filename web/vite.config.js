import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        timeout: 30_000,
        configure(proxy) {
          proxy.on('error', (err) => {
            console.warn('[vite /api proxy]', err?.message || err)
          })
        },
      },
    },
  },
})
