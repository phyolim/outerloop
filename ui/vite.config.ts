import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// base: '/' -> built asset URLs are absolute. Required by path routing: a deep link
// like /ticket/3 must load /assets/x.js, not ./assets/x.js (= /ticket/assets/x.js,
// which the hub's history-API fallback would answer with index.html).
export default defineConfig({
  base: '/',
  plugins: [react()],
  server: {
    // The dev server owns the app routes (SPA fallback); only the JSON seam proxies
    // to a running hub.
    proxy: {
      '/ui': 'http://localhost:8765',
    },
  },
})
