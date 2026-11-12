// Checks platforms/javascript-browser.txt against the installed @sentry/browser.
// The JavaScript SDK ignores options it doesn't know without any warning, so a
// renamed or removed option would quietly stop applying. This type-checks every
// listed name against the SDK's BrowserOptions type instead.
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { expect, it } from "vitest";
import { GENERATED } from "./helpers.js";

const require = createRequire(import.meta.url);
const REPO = join(import.meta.dirname, "..", "..");
const LIST = join(REPO, "sentry-config", "platforms", "javascript-browser.txt");
const TSC = join(REPO, "node_modules", ".bin", "tsc");

it("every listed option exists in the installed SDK", () => {
  const names = readFileSync(LIST, "utf8").split(/\s+/).filter(Boolean);
  const file = join(GENERATED, "option-list-check.ts");
  mkdirSync(GENERATED, { recursive: true });
  writeFileSync(
    file,
    [
      'import type { BrowserOptions } from "@sentry/browser";',
      "export const names: Array<keyof BrowserOptions> = [",
      ...names.map((name) => `  ${JSON.stringify(name)},`),
      "];",
      "",
    ].join("\n"),
  );

  let output = "";
  try {
    execFileSync(TSC, ["--noEmit", "--strict", "--skipLibCheck", "--module", "esnext",
      "--moduleResolution", "bundler", "--target", "es2022", file], { encoding: "utf8" });
  } catch (error) {
    output = `${error.stdout}${error.stderr}`;
  }
  const version = require("@sentry/browser/package.json").version;
  expect(output, `@sentry/browser ${version} doesn't support some listed options. Output: ${output}`).toBe("");
});
