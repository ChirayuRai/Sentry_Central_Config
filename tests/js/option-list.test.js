// Checks sentry-config/options/javascript.txt against the installed SDK. The JavaScript SDK
// ignores options it doesn't know without any warning, so a renamed or removed option would
// quietly stop applying. This type-checks every listed name against BrowserOptions instead,
// which @sentry/react and the @sentry/nextjs client also take.
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { expect, it } from "vitest";
import { GENERATED } from "./helpers.js";

const require = createRequire(import.meta.url);
const REPO = join(import.meta.dirname, "..", "..");
const TSC = join(REPO, "node_modules", ".bin", "tsc");

it("every option in javascript.txt exists in @sentry/browser", () => {
  const names = readFileSync(join(REPO, "sentry-config", "options", "javascript.txt"), "utf8").split(/\s+/).filter(Boolean);
  const file = join(GENERATED, "option-list.ts");
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
  expect(output, `@sentry/browser ${version} doesn't support some options in javascript.txt: ${output}`).toBe("");
});
