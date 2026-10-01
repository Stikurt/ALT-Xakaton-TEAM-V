import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const target = process.env.VITE_BACKEND ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  build: process.env.VITE_MOCK === '1'
    ? { rollupOptions: { output: { inlineDynamicImports: true } }, assetsInlineLimit: 100000000 }
    : undefined,
  server: {
    port: 5173,
    fs: { allow: ['..'] },
    proxy: {
      '/api': target,
      '/health': target,
      '/ws': { target: target.replace('http', 'ws'), ws: true },
    },
  },
})
