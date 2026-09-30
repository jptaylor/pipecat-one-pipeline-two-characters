import path from "path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
      // The cast lives at the repo root, shared with the bot.
      "@cast": path.resolve(__dirname, "../characters.json"),
    },
  },
  // characters.json sits beside client/, not inside it.
  server: { fs: { allow: [".."] } },
})
