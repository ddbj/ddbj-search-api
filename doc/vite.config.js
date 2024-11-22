import { DynamicPublicDirectory } from "vite-multiple-assets";
import FullReload from "vite-plugin-full-reload";
import { defineConfig } from "vite";
// same level as project root
const dirAssets = ["public/**", "tsp-output/@typespec/**"];
// example

export default defineConfig({
  plugins: [
    DynamicPublicDirectory(dirAssets, {
      ssr: false,
    }),
    FullReload("tsp-output/@typespec/openapi3/openapi.yaml", { always: false }),
  ],
  publicDir: false,
  build: {
    emptyOutDir: true,
  },
});
