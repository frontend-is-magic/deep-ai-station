import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

const configuredPort = process.env.LAB_API_PORT ?? '8020';
if (
  !/^[0-9]{1,5}$/.test(configuredPort) ||
  Number(configuredPort) < 1 ||
  Number(configuredPort) > 65535
) {
  throw new Error('LAB_API_PORT must be a valid loopback port');
}
const proxy = {
  '^/api(?:/|$)': {
    target: `http://127.0.0.1:${Number(configuredPort)}`,
    rewrite: (path: string) => path.replace(/^\/api(?=\/|$)/, ''),
  },
};
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { host: '127.0.0.1', port: 5199, strictPort: true, proxy },
  preview: { host: '127.0.0.1', port: 5199, strictPort: true, proxy },
});
