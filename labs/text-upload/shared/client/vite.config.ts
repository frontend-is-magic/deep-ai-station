import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

const port = process.env.LAB_API_PORT ?? '8023';
if (!/^[0-9]{1,5}$/.test(port) || Number(port) < 1 || Number(port) > 65535) {
  throw new Error('LAB_API_PORT must be a valid loopback port');
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: '127.0.0.1',
    port: 5197,
    strictPort: true,
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${port}`,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
});
