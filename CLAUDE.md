# Central Sentry config: context for Claude

## What this is

A Sentry customer wants one place to control Sentry SDK configuration across all their projects. Sentry has no official support for this, so this repo is a reference prototype the user is sending to the customer. It runs in the customer's CI/CD on every build and release. Customer-facing writing should be concise and not fluffy.

## Architecture (decided)

**`sentry-config/sentry_config.yaml`** holds a `default` block plus one block per project.
- `default` has `shared` (snake_case options common to all SDKs), `environments` (shared options per environment), and `native` (per-platform options in each SDK's own names, passed through as-is).
- Each project block has `root` and `platform` (both required, even for new projects), plus optional `shared`, `environments` and `native`.
- Everything except functions goes in the config. Callbacks (`beforeSend*`, `beforeBreadcrumb`), samplers and `transport` are passed in code.

**`generate.py --project X [--release V] [--config path]`** is the CI step.
- It validates the whole config and fails the build on any problem, even one in another project.
- It merges layers in this order: default.shared, default.environments[env], default.native[platform], project.shared, project.environments[env], project.native. Maps merge, lists append, and single values replace.
- It converts shared names to camelCase for JavaScript and drops shared options missing from `platforms/<platform>.txt`. It sets the profiling lifecycle to `"trace"` when a profiling rate is above 0, and sets `release` when `--release` is passed.
- It writes one snapshot JSON (every environment) into the project's `root` and copies the matching bridge next to it.

**Bridges** are `sentry_bridge.js` (for `@sentry/browser`) and `sentry_bridge.py`.
- The calls are `getSentryOptions(project, env, dsn, codeOptions)` and `get_sentry_options(project, env, dsn, **code_options)`.
- They pick the environment from the snapshot at runtime, so a build promoted from staging to production uses production values.
- They build integrations by name. In JavaScript, `replay` becomes `Sentry.replayIntegration(...)`. In Python, the key is the class path under `sentry_sdk.integrations`. `false` disables a default integration, and `true` or an empty value means default options.
- They turn `{regex: ...}` entries into a `RegExp` (JavaScript) or a plain pattern string (Python).
- Code options are applied last, with a warning when they override the config. Integrations passed in code are appended.
- The Python bridge drops options the installed SDK doesn't know, because `init()` raises on them.
- The bridges never throw; they log warnings. CI is the strict layer.

**Why it's built this way:**
- **Pre-merged JSON per project instead of reading the YAML at runtime.** Browsers don't download every project's config, the merge logic exists once, and CI validates the exact output that ships. Switching the source file to JSON wouldn't fix any of these, and it would lose comments.
- **The bridge is copied into each repo by generate.** This was the user's choice for now. The alternative is publishing the bridge as a package and passing the snapshot in.

## Tests

- **pytest (`tests/python`):**
  - Generator tests cover merge rules, every validation error and the CLI. They also compare full output for `tests/fixtures/config.yaml` against `tests/fixtures/expected/*.json`; `UPDATE_GOLDEN=1 pytest` regenerates those.
  - Python bridge tests run against the real SDK with a capture transport.
  - The option-list test checks that every name in `python.txt` exists in `DEFAULT_OPTIONS`. It is a subset check, not equality. SDK options missing from the list only produce a warning.
- **Vitest with jsdom (`tests/js`):**
  - `bridge.test.js` tests the JavaScript bridge against `@sentry/browser`, using the expected snapshots, so it doesn't need Python.
  - `integration-options.test.js` is a separate file because Vitest's module mock throws on missing exports, which breaks the unknown-integration test.
  - `option-list.test.js` type-checks the list against `BrowserOptions` with `tsc`. This is also a subset check.
- **Pinned versions:** sentry-sdk 2.74.0, @sentry/browser 11.11.0, vitest 5.1, typescript 7.0.

## Verified SDK behavior

- Python `sentry_sdk.init()` raises `TypeError` on unknown options. The JavaScript SDK ignores them without any warning.
- JavaScript SDK 11 streams spans by default (`traceLifecycle: 'stream'`), so spans arrive as `span` envelope items rather than `transaction`. `ignoreTransactions` does nothing unless `traceLifecycle` is `'static'`; use `ignoreSpans`. `sendDefaultPii` was removed in favor of `dataCollection`.
- Browser sample rates need their integration: `tracesSampleRate` needs `browserTracing`, the replay rates need `replay`, and profiling needs `browserProfiling`. Python continuous profiling only starts on its own with `profile_lifecycle="trace"`.
- In Vitest with jsdom, Replay registers but never starts, so the tests check its registration and options only.

## Current state of the user's local copy (verify first, then delete this section)

The user's code has diverged from the last zip Claude produced.

1. **The snapshot was renamed to `sentry_local_config.json`.** Confirm both bridges load that name, the test helpers write it (`load_bridge` in `tests/python/conftest.py` and `loadBridge` in `tests/js/helpers.js`), and `.gitignore` lists it. The bridge tests write their own snapshot file, so they won't catch a mismatch on their own.
2. **`generate.py` was based on an older copy.** It lacked `--config`, the rejection of boolean sample rates, and the missing-`default` check, which caused 12 test failures. Fix snippets were given; confirm they're applied.
3. **The `platforms/*.txt` lists were trimmed to only options allowed in the YAML.** `dsn`, `environment`, `beforeSend` and similar were removed, and the user then reported test failures. Not yet diagnosed. In a reproduction against the original code, removing the caller-set names (dsn, environment, release) and the callback, sampler and transport names from both lists left every test passing: 79 pytest and 24 Vitest. The failures probably come from another removed name, such as `integrations`, `profile_lifecycle`/`profileLifecycle`, or an option used in `tests/fixtures/config.yaml`, or from other local edits. Next step: run the suite and read the actual failure output.

## Offered but not done

- A test that runs `generate.py` and then loads the bridge from the folder it wrote to, to catch filename mismatches between generate and the bridges.
- A Node bridge variant (`@sentry/node`; profiling there needs `@sentry/profiling-node`).
- Option lists per SDK major version, with CI picking the list from the project's `package.json` or Python requirements.
