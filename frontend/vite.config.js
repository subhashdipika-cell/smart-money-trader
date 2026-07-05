import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
// In production (npm run build) the dashboard is served by the backend under
// /app, so assets must be referenced relative to that path. The dev server
// (npm run dev) keeps the default "/" base.
export default defineConfig(({ command }) => ({
  plugins: [react()],
  base: command === 'build' ? '/app/' : '/',
}))
