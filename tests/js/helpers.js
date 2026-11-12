import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
export const GENERATED = join(REPO, "tests", ".generated");
export const BRIDGE = join(REPO, "sentry-config", "bridges", "sentry_bridge.js");
export const DSN = "https://public@o0.ingest.sentry.io/0";
export const RELEASE = "1.4.2";

/** A golden snapshot written by generate.py from tests/fixtures/config.yaml. */
export function golden(project) {
  return JSON.parse(readFileSync(join(REPO, "tests", "fixtures", "expected", `${project}.json`), "utf8"));
}

/** A golden snapshot with some values in one environment changed. */
export function snapshotWith(project, env, changes) {
  const snapshot = golden(project);
  Object.assign(snapshot.environments[env], changes);
  return snapshot;
}

/**
 * Put a snapshot next to a copy of the bridge, as generate.py does, and import that copy.
 * Pass a project name for its golden snapshot, or a snapshot object.
 */
export async function loadBridge(snapshot) {
  if (typeof snapshot === "string") snapshot = golden(snapshot);
  const folder = join(GENERATED, randomUUID());
  mkdirSync(folder, { recursive: true });
  writeFileSync(join(folder, "sentry_config.json"), JSON.stringify(snapshot));
  copyFileSync(BRIDGE, join(folder, "sentry_bridge.js"));
  return import(join(folder, "sentry_bridge.js"));
}

/** A transport that records envelopes instead of sending them. */
export function captureTransport() {
  const envelopes = [];
  return {
    transport: () => ({
      send: async (envelope) => {
        envelopes.push(envelope);
        return {};
      },
      flush: async () => true,
    }),
    payloads: (type) =>
      envelopes
        .flatMap(([, items]) => items)
        .filter(([headers]) => headers.type === type)
        .map(([, payload]) => payload),
    // Root span names, whichever way the SDK sends them: as transactions
    // (traceLifecycle "static") or as streamed spans ("stream", the SDK 11 default).
    rootSpanNames: () =>
      envelopes
        .flatMap(([, items]) => items)
        .flatMap(([headers, payload]) => {
          if (headers.type === "transaction") return [payload.transaction];
          if (headers.type === "span") return payload.items.filter((s) => s.is_segment).map((s) => s.name);
          return [];
        }),
  };
}
