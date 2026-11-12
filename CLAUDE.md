# Central Sentry config: context for Claude

## What this is

A Sentry customer wants one place to control Sentry SDK configuration across all their projects. Sentry has no official support for this, so this repo is a reference prototype the user is sending to the customer. It runs in the customer's CI/CD on every build and release. Customer-facing writing should be concise and not fluffy.

## Architecture (decided)

**`sentry-config/sentry_main_config.yaml`** holds a `default` block plus one block per project.
- `default` has `shared` (snake_case options common to all SDKs), `environments` (shared options per environment), and `native` (per-platform options in each SDK's own names, passed through as-is).
- Each project block has `root` and `platform` (both required, even for new projects), plus optional `shared`, `environments` and `native`.
- Platforms: `python` and `django` use the Python bridge and snake_case names. `javascript-browser`, `react` and `next-js` use the JavaScript bridge and camelCase names. `next-js` is client side only for now.
- Everything except functions goes in the config. Callbacks (`beforeSend*`, `beforeBreadcrumb`), samplers and `transport` are passed in code.

**`generate.py --project X [--release V] [--config path]`** is the CI step. `--config` defaults to `sentry_main_config.yaml` next to the script.
- It validates the whole config and fails the build on any problem, even one in another project. `integrations` must be a map.
- It merges layers in this order: default.shared, default.environments[env], default.native[platform], project.shared, project.environments[env], project.native. Maps merge, lists append, and single values replace.
- It converts shared names to camelCase for JavaScript platforms (decided by bridge in `BRIDGES`, not platform name) and drops shared options missing from `platforms/<platform>.txt`. It sets the profiling lifecycle to `"trace"` when a profiling rate is above 0, and sets `release` when `--release` is passed.
- For every JavaScript platform, it fails when a browser sample rate is set without its integration. `DEFAULT_INTEGRATIONS` lists the ones an SDK adds on its own (`@sentry/nextjs` adds `browserTracing`).
- It writes `sentry_local_config.json` (every environment) into the project's `root` and copies the matching bridge next to it. In the JavaScript copy, `from "@sentry/browser"` is rewritten to the platform's package from `JS_PACKAGES` (`@sentry/react`, `@sentry/nextjs`).

**Bridges** are `sentry_bridge.js` and `sentry_bridge.py`.
- The calls are `getSentryOptions(project, env, dsn, codeOptions)` and `get_sentry_options(project, env, dsn, **code_options)`.
- They pick the environment from the snapshot at runtime, so a build promoted from staging to production uses production values.
- They build integrations by name. In JavaScript, `replay` becomes `Sentry.replayIntegration(...)`. In Python, the key is the class path under `sentry_sdk.integrations`. `false` disables a default integration, and `true` or an empty value (`null`) means default options.
- They turn `{regex: ...}` entries into a `RegExp` (JavaScript) or a plain pattern string (Python).
- Code options are applied last, with a warning when they override the config. Integrations passed in code are appended, so they replace a configured integration with the same name.
- The Python bridge drops options the installed SDK doesn't know, because `init()` raises on them.
- The bridges never throw; they log warnings. CI is the strict layer.

**Why it's built this way:**
- **Pre-merged JSON per project instead of reading the YAML at runtime.** Browsers don't download every project's config, the merge logic exists once, and CI validates the exact output that ships. Switching the source file to JSON wouldn't fix any of these, and it would lose comments.
- **The bridge is copied into each repo by generate.** This was the user's choice for now. The alternative is publishing the bridge as a package and passing the snapshot in.
- **One JavaScript bridge with its import rewritten,** rather than one file per SDK or passing `Sentry` into the call. `@sentry/react` and `@sentry/nextjs` re-export the browser integrations under the same names, and Next.js needs its own `browserTracingIntegration`.

## Tests

- **pytest (`tests/python`):**
  - Generator tests cover merge rules, every validation error and the CLI. They also compare full output for `tests/fixtures/config.yaml` against `tests/fixtures/expected/*.json`; `UPDATE_GOLDEN=1 pytest` regenerates those. The fixture has at least one project per platform; `PLATFORMS` in `conftest.py` is read from it.
  - "What a project gets" tests run `generate.py`, then load the Python bridge from the folder it wrote, or check that the JavaScript copy's imports resolve there and name the platform's SDK. These catch filename mismatches between generate and the bridges.
  - Python bridge tests run against the real SDK with a capture transport.
  - The option-list tests check that every name in each Python list (`python.txt`, `django.txt`) exists in `DEFAULT_OPTIONS`. It is a subset check, not equality. SDK options missing from a list only produce a warning, which skips `CODE_ONLY` and `CALLER_SET` names.
- **Vitest with jsdom (`tests/js`):**
  - `bridge.test.js` tests the JavaScript bridge against `@sentry/browser` and `@sentry/react`, using the expected snapshots, so it doesn't need Python. `loadBridge` rewrites the import the same way generate does.
  - `@sentry/nextjs` isn't installed because it pulls in Next.js and the Node SDK. The pytest tests cover the Next.js bridge copy.
  - `integration-options.test.js` is a separate file because Vitest's module mock throws on missing exports, which breaks the unknown-integration test.
  - `option-list.test.js` type-checks each JavaScript list against `BrowserOptions` with `tsc`. This is also a subset check.
- **Pinned versions:** sentry-sdk 2.74.0, @sentry/browser 11.11.0, @sentry/react 11.11.0, react 19.3.1, vitest 5.1, typescript 7.0.
- **Last run:** 108 pytest, 29 Vitest, all passing.

## Verified SDK behavior

- Python `sentry_sdk.init()` raises `TypeError` on unknown options. The JavaScript SDK ignores them without any warning.
- sentry-sdk 2.74 deprecates `send_default_pii` in favor of `data_collection` (a `DeprecationWarning` at init).
- JavaScript SDK 11 streams spans by default (`traceLifecycle: 'stream'`), so spans arrive as `span` envelope items rather than `transaction`. `ignoreTransactions` does nothing unless `traceLifecycle` is `'static'`; use `ignoreSpans`. `sendDefaultPii` was removed in favor of `dataCollection`.
- Browser sample rates need their integration: `tracesSampleRate` needs `browserTracing`, the replay rates need `replay`, and profiling needs `browserProfiling`. Python continuous profiling only starts on its own with `profile_lifecycle="trace"`.
- `@sentry/react` 11.11: `export *` from `@sentry/browser`; `init` takes `BrowserOptions` from `@sentry/browser` and only adds SDK metadata, so default integrations are the browser ones. React Router tracing integrations take router functions (code only) and are named `BrowserTracing`.
- `@sentry/nextjs` 11.11 client (`index.client.js`, picked by the `browser` export condition): `export *` from `@sentry/react` with its own `browserTracingIntegration`. `init` takes `@sentry/react`'s `BrowserOptions` and adds `browserTracingIntegration` and a stack-frame normalization integration to the defaults.
- `@sentry/core` keeps the last integration of each name, but a default instance never replaces one the user passed.
- `transportOptions.bufferSize` (JavaScript counterpart of Python's `transport_queue_size`, default 64) caps both the client's event processing and the transport's open requests; anything over is dropped as `queue_overflow`, not queued. Nested keys aren't converted, so it goes in `native`. `Sentry.close(0)` and `flush(0)` wait forever on open requests, so tests that hold requests open must release them.
- In Vitest with jsdom, Replay registers but never starts, so the tests check its registration and options only.

## Open items in the user's config

- `default.native.django` sets `send_default_pii`. It's deprecated but still works, and the user chose to keep it.
- The JavaScript lists still include `stackParser` and `python.txt` includes `custom_repr`, though generate rejects both as code-only anyway.

## Offered but not done

- Next.js server and edge side: needs a Node-side bridge and option lists for `@sentry/nextjs` server options.
- A Node bridge variant (`@sentry/node`; profiling there needs `@sentry/profiling-node`).
- Option lists per SDK major version, with CI picking the list from the project's `package.json` or Python requirements.
