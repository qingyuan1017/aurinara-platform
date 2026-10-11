import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  const proxyTarget = env.VITE_PROXY_TARGET || 'http://localhost:8002'
  return {
    plugins: [react()],
    define: {
      global: 'globalThis',
    },
    server: {
      port: 5174,
      proxy: {
        '/v1': proxyTarget,
        '/health': proxyTarget,
      },
    },
  }
})
