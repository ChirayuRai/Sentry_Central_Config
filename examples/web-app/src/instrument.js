import * as Sentry from "@sentry/browser";
import { getSentryOptions } from "./sentry_bridge.js"; // written by generate.py in CI

// The environment is picked when the page loads, so one build can move from staging to production.
const ENVIRONMENTS = { "app.example.com": "production", "staging.app.example.com": "staging" };
const environment = ENVIRONMENTS[window.location.hostname] ?? "development";

Sentry.init(
  getSentryOptions("web-app", environment, import.meta.env.VITE_SENTRY_DSN, {
    // Optional: callbacks and anything else that can't be written as data.
    beforeSend(event) {
      return event;
    },
  }),
);
