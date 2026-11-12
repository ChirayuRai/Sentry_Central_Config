// Checks the JavaScript option lists in sentry-config/platforms/ against the installed SDKs.
// The JavaScript SDK ignores options it doesn't know without any warning, so a
// renamed or removed option would quietly stop applying. This type-checks every
// listed name against the SDK's BrowserOptions type instead.
//
// @sentry/react's init takes @sentry/browser's BrowserOptions, and @sentry/nextjs's
// client init takes @sentry/react's. @sentry/nextjs isn't installed because it pulls in
// Next.js, so next-js.txt is checked against the @sentry/browser type it uses.
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { expect, it } from "vitest";
import { GENERATED } from "./helpers.js";

const require = createRequire(import.meta.url);
const REPO = join(import.meta.dirname, "..", "..");
const TSC = join(REPO, "node_modules", ".bin", "tsc");
const LISTS = { "javascript-browser": "@sentry/browser", react: "@sentry/react", "next-js": "@sentry/browser" };

for (const [platform, sdk] of Object.entries(LISTS)) {
  it(`every option in ${platform}.txt exists in ${sdk}`, () => {
    const names = readFileSync(join(REPO, "sentry-config", "platforms", `${platform}.txt`), "utf8")
      .split(/\s+/)
      .filter(Boolean);
    const file = join(GENERATED, `option-list-${platform}.ts`);
    mkdirSync(GENERATED, { recursive: true });
    writeFileSync(
      file,
      [
        `import type { BrowserOptions } from "${sdk}";`,
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
    const version = require(`${sdk}/package.json`).version;
    expect(output, `${sdk} ${version} doesn't support some options in ${platform}.txt. Output: ${output}`).toBe("");
  });
}
