// Types for sentry_bridge.js, for TypeScript projects. Copied in by CI. Do not edit in the project.
// generate.py points this import at the project's SDK, as it does in the bridge.
import type { BrowserOptions } from "@sentry/browser";

/**
 * The central config's options for this project and environment, with codeOptions applied last.
 * @param codeOptions callbacks and anything else that can't be written as data.
 */
export function getSentryOptions(
  project: string,
  environment: string,
  dsn: string | undefined,
  codeOptions?: BrowserOptions,
): BrowserOptions;
