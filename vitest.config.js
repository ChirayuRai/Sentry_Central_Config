import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["tests/js/**/*.test.js"],
    environment: "jsdom",
    globalSetup: "tests/js/global-setup.js",
  },
});
