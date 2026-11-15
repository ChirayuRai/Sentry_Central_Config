// Tests for the JavaScript bridge, run against the installed @sentry/browser and @sentry/react.
// The bridge is loaded the way a project gets it: a copy sitting next to a
// sentry_local_config.json snapshot. Snapshots come from tests/fixtures/expected/.
// @sentry/nextjs isn't installed because it pulls in Next.js; the Python tests
// check that Next.js projects get a bridge importing it.
import { afterEach, describe, expect, it, vi } from "vitest";
import * as Sentry from "@sentry/browser";
import * as SentryReact from "@sentry/react";
import { DSN, RELEASE, captureTransport, golden, loadBridge, snapshotWith } from "./helpers.js";

afterEach(async () => {
  await Sentry.close(0);
  Sentry.getCurrentScope().setClient(undefined);
  vi.restoreAllMocks();
});

const integrationNames = (options) =>
  options.integrations(Sentry.getDefaultIntegrations({})).map((integration) => integration.name);

async function init(snapshot, project, env, codeOptions = {}) {
  const { getSentryOptions } = await loadBridge(snapshot);
  const sent = captureTransport();
  Sentry.init(getSentryOptions(project, env, DSN, { transport: sent.transport, ...codeOptions }));
  return sent;
}

describe("what the bridge returns", () => {
  it("returns the options for the environment", async () => {
    const { getSentryOptions } = await loadBridge("web-app");
    const options = getSentryOptions("web-app", "production", DSN);
    expect(options).toMatchObject({
      tracesSampleRate: 0.2,
      replaysSessionSampleRate: 0.05,
      maxBreadcrumbs: 50,
      release: RELEASE,
      dsn: DSN,
      environment: "production",
    });
  });

  it("turns regex entries into RegExp objects and keeps plain strings", async () => {
    const { getSentryOptions } = await loadBridge("web-app");
    const { ignoreErrors, denyUrls, tracePropagationTargets } = getSentryOptions("web-app", "production", DSN);
    expect(ignoreErrors).toEqual(["ResizeObserver loop limit exceeded", /^AbortError/, "Non-Error promise rejection captured"]);
    expect(denyUrls[0]).toBeInstanceOf(RegExp);
    expect(tracePropagationTargets[0].test("https://api.example.com/users")).toBe(true);
  });

  it("adds configured integrations and removes disabled defaults", async () => {
    const { getSentryOptions } = await loadBridge("web-app");
    const names = integrationNames(getSentryOptions("web-app", "production", DSN));
    expect(names).toEqual(expect.arrayContaining(["BrowserTracing", "Replay", "GlobalHandlers"]));
    expect(names).not.toContain("Dedupe");
  });

  it("skips an unknown integration with a warning", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const snapshot = snapshotWith("web-new", "production", { integrations: { madeUp: {} } });
    const { getSentryOptions } = await loadBridge(snapshot);
    getSentryOptions("web-new", "production", DSN);
    expect(warn).toHaveBeenCalledWith('[sentry] unknown integration "madeUp", skipping');
  });

  it("skips an invalid regex with a warning", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const snapshot = snapshotWith("web-new", "production", { denyUrls: [{ regex: "(" }, { regex: "^x" }] });
    const { getSentryOptions } = await loadBridge(snapshot);
    expect(getSentryOptions("web-new", "production", DSN).denyUrls).toEqual([/^x/]);
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('[sentry] skipping invalid regex "("'));
  });

  it("warns on an unknown environment and leaves the SDK defaults", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { getSentryOptions } = await loadBridge("web-app");
    const options = getSentryOptions("web-app", "qa", DSN);
    expect(warn).toHaveBeenCalledWith('[sentry] no config for environment "qa"');
    expect(Object.keys(options).sort()).toEqual(["dsn", "environment", "integrations"]);
    expect(integrationNames(options)).toEqual(Sentry.getDefaultIntegrations({}).map((i) => i.name));
  });

  it("warns when the project name doesn't match the snapshot", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { getSentryOptions } = await loadBridge("web-app");
    getSentryOptions("checkout", "production", DSN);
    expect(warn).toHaveBeenCalledWith('[sentry] config is for "web-app", not "checkout"');
  });

  it("lets code override the config, with a warning", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { getSentryOptions } = await loadBridge("web-app");
    expect(getSentryOptions("web-app", "production", DSN, { maxBreadcrumbs: 5 }).maxBreadcrumbs).toBe(5);
    expect(warn).toHaveBeenCalledWith('[sentry] code overrides central config for "maxBreadcrumbs"');
  });

  it("appends integrations passed in code after the configured ones", async () => {
    const { getSentryOptions } = await loadBridge("web-app");
    const names = integrationNames(
      getSentryOptions("web-app", "production", DSN, { integrations: [Sentry.feedbackIntegration({ autoInject: false })] }),
    );
    expect(names.slice(-3)).toEqual(["BrowserTracing", "Replay", "Feedback"]);
  });
});

describe("what the SDK ends up with", () => {
  for (const project of ["web-app", "web-new"]) {
    for (const env of ["production", "staging"]) {
      it(`initializes with ${project} / ${env}`, async () => {
        await init(project, project, env);
        const client = Sentry.getClient();
        const { integrations, ignoreErrors, denyUrls, tracePropagationTargets, ...values } =
          golden(project).environments[env];
        expect(client.getOptions()).toMatchObject(values);
        expect(client.getIntegrationByName("BrowserTracing")).toBeDefined();
        expect(client.getIntegrationByName("Replay")).toBeDefined();
        expect(Boolean(client.getIntegrationByName("Dedupe"))).toBe(project !== "web-app");
      });
    }
  }

  for (const env of ["production", "staging"]) {
    it(`initializes @sentry/react with react-app / ${env}`, async () => {
      const { getSentryOptions } = await loadBridge("react-app");
      SentryReact.init(getSentryOptions("react-app", env, DSN));
      const client = SentryReact.getClient();
      const { integrations, ...values } = golden("react-app").environments[env];
      expect(client.getOptions()).toMatchObject(values);
      expect(client.getOptions()._metadata.sdk.name).toBe("sentry.javascript.react");
      expect(client.getIntegrationByName("BrowserTracing")).toBeDefined();
      expect(client.getIntegrationByName("Replay")).toBeDefined();
    });
  }
});

describe("what actually gets sent", () => {
  it("tags errors with the release and environment", async () => {
    const sent = await init("web-app", "web-app", "staging");
    Sentry.captureException(new Error("boom"));
    await Sentry.flush(1000);
    const [event] = sent.payloads("event");
    expect(event).toMatchObject({ release: RELEASE, environment: "staging" });
  });

  it("drops errors that match ignoreErrors", async () => {
    const sent = await init("web-app", "web-app", "staging");
    Sentry.captureException(new Error("ResizeObserver loop limit exceeded")); // default, substring
    Sentry.captureException(Object.assign(new Error("The user aborted a request."), { name: "AbortError" })); // default, regex
    Sentry.captureException(new Error("Non-Error promise rejection captured with value: x")); // added by the project
    Sentry.captureException(new Error("real bug"));
    await Sentry.flush(1000);
    expect(sent.payloads("event").map((e) => e.exception.values[0].value)).toEqual(["real bug"]);
  });

  it("runs a beforeSend passed in code", async () => {
    const beforeSend = (event) => ({ ...event, tags: { ...event.tags, checked: "yes" } });
    const sent = await init("web-app", "web-app", "staging", { beforeSend });
    Sentry.captureException(new Error("boom"));
    await Sentry.flush(1000);
    expect(sent.payloads("event")[0].tags.checked).toBe("yes");
  });
});
