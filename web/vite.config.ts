import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true } },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test-setup.ts',
    // The theme test reads the two app stylesheets; other CSS stays stubbed in tests.
    css: { include: [/src\/(style|screens)\.css/] },
  },
});
