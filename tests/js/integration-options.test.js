// Checks the options the bridge passes to each integration factory.
// Kept in its own file because it mocks @sentry/browser, and Vitest's module
// mock throws on exports that don't exist, which would break the
// unknown-integration test in bridge.test.js.
import { afterEach, expect, it, vi } from "vitest";
import * as Sentry from "@sentry/browser";
import { DSN, loadBridge, snapshotWith } from "./helpers.js";

vi.mock("@sentry/browser", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    replayIntegration: vi.fn(actual.replayIntegration),
    browserTracingIntegration: vi.fn(actual.browserTracingIntegration),
  };
});

afterEach(() => {
  vi.clearAllMocks();
});

it("passes the merged integration options to the integration", async () => {
  const { getSentryOptions } = await loadBridge("web-app");
  getSentryOptions("web-app", "production", DSN);
  // default.native sets maskAllText: true and blockAllMedia: true; web-app overrides maskAllText.
  expect(Sentry.replayIntegration).toHaveBeenCalledWith({ maskAllText: false, blockAllMedia: true });
  expect(Sentry.browserTracingIntegration).toHaveBeenCalledWith({});
});

it("uses default options for an integration set to true or left empty", async () => {
  const snapshot = snapshotWith("web-new", "production", {
    integrations: { browserTracing: null, replay: true },
  });
  const { getSentryOptions } = await loadBridge(snapshot);
  const options = getSentryOptions("web-new", "production", DSN);
  const names = options.integrations(Sentry.getDefaultIntegrations({})).map((i) => i.name);
  expect(names).toEqual(expect.arrayContaining(["BrowserTracing", "Replay"]));
  expect(Sentry.browserTracingIntegration).toHaveBeenCalledWith(undefined);
  expect(Sentry.replayIntegration).toHaveBeenCalledWith(undefined);
});
