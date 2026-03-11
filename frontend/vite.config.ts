import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";
import { componentTagger } from "lovable-tagger";

export default defineConfig(({ mode }) => ({
  server: {
    host: "0.0.0.0",
    port: 4014,
    hmr: {
      overlay: false,
    },
    proxy: {
      '/api/joiner': {
        target: 'http://127.0.0.1:3114',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api\/joiner/, ''),
      },
      '/api': {
        target: 'http://127.0.0.1:3113',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  plugins: [react(), mode === "development" && componentTagger()].filter(Boolean),
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
}));
