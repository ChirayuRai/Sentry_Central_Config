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
  if (vi.isMockFunction(console.warn)) console.warn.mockRestore();
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

it("doesn't build a configured integration that code replaces", async () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  const { getSentryOptions } = await loadBridge("web-app");
  const fromCode = Sentry.replayIntegration({ maskAllText: true });
  const options = getSentryOptions("web-app", "production", DSN, { integrations: [fromCode] });
  // Only the code's instance is built: in a browser, a second Replay instance throws.
  expect(Sentry.replayIntegration).toHaveBeenCalledTimes(1);
  expect(options.integrations(Sentry.getDefaultIntegrations({}))).toContain(fromCode);
  expect(warn).toHaveBeenCalledWith('[sentry] code replaces the configured integration "replay"');
});

it("skips an integration that throws while it's set up", async () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  Sentry.replayIntegration.mockImplementationOnce(() => {
    throw new Error("boom");
  });
  const { getSentryOptions } = await loadBridge("web-app");
  const names = getSentryOptions("web-app", "production", DSN)
    .integrations(Sentry.getDefaultIntegrations({}))
    .map((integration) => integration.name);
  expect(names).not.toContain("Replay");
  expect(warn).toHaveBeenCalledWith('[sentry] skipping integration "replay": boom');
});
