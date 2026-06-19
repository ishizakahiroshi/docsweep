import { defineConfig } from 'vite';
import { resolve } from 'path';

export default defineConfig({
  build: {
    lib: {
      entry: resolve(__dirname, 'src/app.ts'),
      name: 'DocsweepApp',
      formats: ['iife'],
      fileName: () => 'app.js',
    },
    outDir: 'docsweep/server/static',
    emptyOutDir: false,
    minify: false,
  },
});
