import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/l2": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/l3": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/l4": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
      "/l5": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
})
