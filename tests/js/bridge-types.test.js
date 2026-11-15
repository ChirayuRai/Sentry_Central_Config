// Type-checks sentry_bridge.d.ts the way a TypeScript project uses it: strict, without
// allowJs (Vite's react-ts template, say), so the bridge import resolves to the types file.
// @sentry/nextjs isn't installed; its client exports the same BrowserOptions type.
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, it } from "vitest";
import { GENERATED } from "./helpers.js";

const REPO = join(import.meta.dirname, "..", "..");
const TSC = join(REPO, "node_modules", ".bin", "tsc");

for (const sdk of ["@sentry/browser", "@sentry/react"]) {
  it(`lets a TypeScript project pass the options to ${sdk}'s init`, () => {
    const folder = join(GENERATED, `types-${sdk.replace(/\W/g, "-")}`);
    mkdirSync(folder, { recursive: true });
    const types = readFileSync(join(REPO, "sentry-config", "bridges", "sentry_bridge.d.ts"), "utf8")
      .replaceAll('from "@sentry/browser"', `from "${sdk}"`);
    writeFileSync(join(folder, "sentry_bridge.d.ts"), types);
    writeFileSync(
      join(folder, "app.ts"),
      [
        `import * as Sentry from "${sdk}";`,
        `import { getSentryOptions } from "./sentry_bridge.js";`,
        "",
        'Sentry.init(getSentryOptions("web-app", "production", undefined, { beforeSend: (event) => event }));',
        "",
      ].join("\n"),
    );

    let output = "";
    try {
      execFileSync(TSC, ["--noEmit", "--strict", "--skipLibCheck", "--module", "esnext",
        "--moduleResolution", "bundler", "--target", "es2022", join(folder, "app.ts")], { encoding: "utf8" });
    } catch (error) {
      output = `${error.stdout}${error.stderr}`;
    }
    expect(output).toBe("");
  });
}
