import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", rewrite: (path) => path.replace(/^\/api(?=\/|$)/, "") },
    },
    // The Python/GPU dependencies can contain hundreds of thousands of files.
    watch: { ignored: ["**/backend/**", "**/.venv/**", "**/data/**"] },
    fs: { deny: [".env", ".env.*", "*.{crt,pem}", "**/.git/**", "**/keys.txt"] },
  },
});
