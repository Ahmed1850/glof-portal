import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          const norm = id.replace(/\\/g, '/');
          if (!norm.includes('/node_modules/')) return undefined;

          if (
            norm.includes('/node_modules/leaflet/') ||
            norm.includes('/node_modules/react-leaflet/') ||
            norm.includes('/node_modules/@react-leaflet/')
          ) {
            return 'leaflet';
          }
          if (norm.includes('/node_modules/recharts/')) {
            return 'recharts';
          }
          if (
            norm.includes('/node_modules/victory-vendor/') ||
            norm.includes('/node_modules/d3-') ||
            norm.includes('/node_modules/recharts-scale/') ||
            norm.includes('/node_modules/decimal.js-light/') ||
            norm.includes('/node_modules/internmap/')
          ) {
            return 'd3-vendor';
          }
          if (
            norm.includes('/node_modules/jspdf/') ||
            norm.includes('/node_modules/jspdf-autotable/')
          ) {
            return 'jspdf';
          }
          if (
            norm.includes('/node_modules/framer-motion/') ||
            norm.includes('/node_modules/motion-dom/') ||
            norm.includes('/node_modules/motion-utils/')
          ) {
            return 'framer-motion';
          }
          if (
            norm.includes('/node_modules/react/') ||
            norm.includes('/node_modules/react-dom/') ||
            norm.includes('/node_modules/scheduler/')
          ) {
            return 'react-vendor';
          }
          return undefined;
        },
      },
    },
  },
});
