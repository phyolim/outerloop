import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// base: './' -> built asset URLs are relative so the Python hub can serve dist/ from '/'
export default defineConfig({
  base: './',
  plugins: [react()],
  server: {
    proxy: {
      '/ui': 'http://localhost:8765',
      '/ticket': 'http://localhost:8765',
      '/decisions': 'http://localhost:8765',
    },
  },
})
