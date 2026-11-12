// Copied in by CI. Do not edit in the project.
// generate.py points this import at the project's SDK: @sentry/browser, @sentry/react or
// @sentry/nextjs. For Next.js, import this file from client code only (instrumentation-client).
import * as Sentry from "@sentry/browser";
import snapshot from "./sentry_local_config.json";

// { regex: "..." } in the config becomes a RegExp; plain strings stay substring matches.
function revive(value) {
  if (Array.isArray(value)) return value.map(revive);
  if (value && typeof value === "object") {
    const keys = Object.keys(value);
    if (keys.length === 1 && keys[0] === "regex") return new RegExp(value.regex);
    return Object.fromEntries(keys.map((k) => [k, revive(value[k])]));
  }
  return value;
}

// { replay: { maskAllText: true }, dedupe: false } -> integration instances.
function buildIntegrations(config = {}, fromCode = []) {
  const disabled = new Set();
  const configured = [];
  for (const [name, options] of Object.entries(config)) {
    if (options === false) {
      disabled.add(name.toLowerCase());
      continue;
    }
    const factory = Sentry[`${name}Integration`];
    if (typeof factory !== "function") {
      console.warn(`[sentry] unknown integration "${name}", skipping`);
      continue;
    }
    // true, or an empty value in the YAML (null), means the integration's default options.
    configured.push(factory(options === true || options === null ? undefined : options));
  }
  return (defaults) => [
    ...defaults.filter((i) => !disabled.has(i.name.toLowerCase())),
    ...configured,
    ...fromCode,
  ];
}

/**
 * @param codeOptions callbacks and anything else that can't live in YAML,
 *   e.g. { beforeSend, integrations: [Sentry.feedbackIntegration()] }
 */
export function getSentryOptions(project, environment, dsn, codeOptions = {}) {
  if (project !== snapshot.project) {
    console.warn(`[sentry] config is for "${snapshot.project}", not "${project}"`);
  }
  const resolved = snapshot.environments[environment];
  if (!resolved) {
    console.warn(`[sentry] no config for environment "${environment}"`);
  }
  const { integrations, ...options } = revive(resolved ?? {});
  const { integrations: codeIntegrations = [], ...fromCode } = codeOptions;

  for (const key of Object.keys(fromCode)) {
    if (key in options) console.warn(`[sentry] code overrides central config for "${key}"`);
  }

  return {
    ...options,
    ...fromCode,
    dsn,
    environment,
    integrations: buildIntegrations(integrations, codeIntegrations),
  };
}
