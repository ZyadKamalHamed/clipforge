import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Built into the Python package, whose server fills in a fresh CSP nonce and the theme on every request.
export default defineConfig({
  base: "/",
  plugins: [react(), tailwindcss()],
  html: { cspNonce: "__CSP_NONCE__" },
  build: { outDir: "../clipforge/server/static", emptyOutDir: true },
});
