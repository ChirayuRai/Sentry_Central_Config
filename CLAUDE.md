# Central Sentry config: notes for AI coding assistants

## What this is

A reference implementation of one Sentry SDK config shared by every repo, so every team starts from the same baseline and changes reach each app with its next build. Sentry has no official feature for this. `README.md` covers setup and use. This file covers how the code fits together and the SDK facts it relies on. Keep user-facing writing concise.

## Layout

- `sentry-config/` is what the central repo ships:
  - `sentry_main_config.yaml`: the config
  - `generate.py`: the CI step
  - `bridges/`: the files copied into each project
  - `platforms/*.txt`: the option names allowed per platform
  - `requirements.txt`: PyYAML, for app pipelines
- `examples/`: init files for a browser app and a Python service. Generate writes into them.
- `tests/`: pytest in `tests/python` and Vitest in `tests/js`.
  - `tests/fixtures/config.yaml` covers every merge rule and platform.
  - `tests/fixtures/expected/` holds its generated output.
- `.github/workflows/check.yml`: runs `--check` and both suites.

## Design

**The config** is a `default` block (`shared`, `environments`, `native`) plus one block per project. Each project block requires `root` and `platform`; `shared`, `environments` and `native` are optional.
- Platforms: `python` and `django` use the Python bridge and snake_case names. `javascript-browser`, `react` and `next-js` use the JavaScript bridge and camelCase names. `next-js` is client side only.
- Everything except functions goes in the config. Callbacks, samplers and the transport are passed in code. `dsn` and `environment` come from the bridge call, and `release` from `--release`.

**`generate.py`** runs as `--project X [--release V] [--config path]` in each build, and as `--check` in the central repo's CI.
- **Loading:** it reads the YAML with `UniqueKeyLoader`, so a duplicate key fails.
- **Validation** covers the whole config:
  - allowed keys and map shapes
  - option names against `platforms/<platform>.txt`, with a closest-name suggestion
  - `CODE_ONLY`, `CALLER_SET` and `REPLACED` names
  - sample-rate ranges and the types in `LIST_OPTIONS`
  - regexes, with `{regex}` allowed only where Python reads patterns (`PYTHON_PATTERNS`)
  - integration name shapes (`INTEGRATION_NAMES`)
- **Merging:** layers merge in this order: default.shared, default.environments[env], default.native[platform], project.shared, project.environments[env], project.native. Maps merge, lists append, and single values replace.
- **Naming:** it converts shared names for JavaScript, deciding by bridge in `BRIDGES` rather than by platform name. It drops shared options a platform lacks, and sets the profile lifecycle to `"trace"` when a profile rate is above 0.
- **Resolved checks:** it resolves every project, plus a new project on each platform in `default.native`. It checks that browser sample rates have their integration; `DEFAULT_INTEGRATIONS` lists the ones an SDK adds itself. Any problem fails every build.
- **Output:** it writes `sentry_local_config.json` into the project's `root` and copies the bridge next to it. JavaScript projects also get `sentry_bridge.d.ts`. In the copies, `from "@sentry/browser"` is rewritten to the platform's package (`JS_PACKAGES`).

**The bridges** are `getSentryOptions(project, env, dsn, codeOptions)` and `get_sentry_options(project, env, dsn, **code_options)`.
- They pick the environment from the snapshot at runtime.
- They build integrations by name.
  - `false` disables a default integration, and `true`, `{}` or null means default options.
  - An integration passed in code replaces the configured one of the same name, which then isn't built.
- They turn `{regex}` into a `RegExp` in JavaScript, or into its pattern string in Python.
- Code options apply last, with a warning when they override the config. The Python bridge drops options the installed SDK lacks, since `init()` raises on them.
- They warn instead of throwing on config the installed SDK can't use. Generate is the strict layer.

**Why it's built this way**
- **Pre-merged JSON per project instead of reading the YAML at runtime:** browsers don't download every project's config, the merge logic exists once, and CI validates the exact output that ships. YAML keeps comments.
- **Distribution at build time fits the goal, consistency:** each build ships the config it was tested with. Fetching at runtime would add a startup dependency.
- **Generate copies the bridge in**, so there's no package to version or publish.
- **One JavaScript bridge with its import rewritten:** `@sentry/react` and `@sentry/nextjs` re-export the browser integrations under the same names, and Next.js needs its own `browserTracingIntegration`.

## Changing things

- **A new option:** add its SDK name to `platforms/<platform>.txt`. The option-list tests check that it exists in the pinned SDK. If it takes a list, add it to `LIST_OPTIONS`.
- **A new platform:** add it to `BRIDGES` (and to `JS_PACKAGES` for JavaScript). Then add `platforms/<platform>.txt` and a project for it in `tests/fixtures/config.yaml`.
- **Generator output that changes on purpose:** run `UPDATE_GOLDEN=1 pytest`, then review the diff in `tests/fixtures/expected/`.
- **The README's "Each app repo" step:** `tests/python/test_across_repos.py` runs it as written, so keep its placeholders: `<central config repo>`, `<name>` and `<version>`.

## Tests

- pytest (`tests/python`):
  - `test_generate.py` covers:
    - merge rules, every validation message, and the CLI
    - golden output for the fixture
    - `--check` on the shipped config
    - "what a project gets", which loads the bridge from the folder generate wrote
  - `test_bridge.py` runs the Python bridge against the real SDK with a capture transport.
  - `test_option_lists.py` checks that every name in `python.txt` and `django.txt` exists in `DEFAULT_OPTIONS`. It's a subset check: SDK options missing from a list only produce a warning.
  - `test_across_repos.py` extracts the README's pipeline step from `README.md` and runs it in a separate app repo against a central git repo. Git runs without the user's global config, and the step's `pip install` is skipped.
- Vitest with jsdom (`tests/js`):
  - `bridge.test.js` runs the JavaScript bridge against `@sentry/browser` and `@sentry/react`. It uses the expected snapshots, so it doesn't need Python.
  - `integration-options.test.js` mocks the integration factories. It's a separate file because Vitest's module mock throws on missing exports.
  - `option-list.test.js` and `bridge-types.test.js` type-check the option lists and `sentry_bridge.d.ts` with `tsc`.
  - `@sentry/nextjs` isn't installed because it pulls in Next.js. The pytest tests cover the Next.js bridge copy.
- Pinned versions: sentry-sdk 2.74.0, PyYAML 6.0.3, @sentry/browser and @sentry/react 11.11.0, react 19.3.1, vitest 5.1, typescript 7.0.

## Verified SDK behavior

- **Unknown options:** Python `sentry_sdk.init()` raises `TypeError` on them. The JavaScript SDK ignores them without a warning.
- **sentry-sdk 2.74 deprecations:**
  - `send_default_pii` is deprecated in favor of `data_collection`, a map with keys such as `user_info`, `cookies`, `http_headers` and `http_bodies`. It logs a `DeprecationWarning` at init.
  - `enable_tracing` is deprecated in favor of `traces_sample_rate`.
- **Python string matching:**
  - `ignore_errors` compares strings to the exception's class name or module-qualified name, exactly.
  - `trace_propagation_targets` and some integration options, such as `exclude_beat_tasks`, read strings as regular expressions.
- **JavaScript SDK 11:**
  - Spans stream by default (`traceLifecycle: 'stream'`), so they arrive as `span` envelope items. `ignoreTransactions` does nothing unless `traceLifecycle` is `'static'`; use `ignoreSpans`.
  - `sendDefaultPii` was removed in favor of `dataCollection`.
  - A string `ignoreErrors` is iterated as single-character patterns, which drops nearly every error.
  - The global handler reports non-error rejections as "Non-Error promise rejection captured with value: …".
- **Integrations that sample rates need:**
  - `tracesSampleRate` needs `browserTracing`, the replay rates need `replay`, and profiling needs `browserProfiling`.
  - Profiling also needs the page served with `Document-Policy: js-profiling`, and in `trace` mode it only runs during sampled root spans.
  - Python continuous profiling only starts on its own with `profile_lifecycle="trace"`.
- **Trace propagation:** setting `tracePropagationTargets` replaces the same-origin default. For same-origin requests the SDK also matches the targets against the path, so `{regex: "^/"}` keeps them.
- **Replay:** a second Replay instance throws "Multiple Sentry Session Replay instances are not supported" in a browser. jsdom skips the check, and in jsdom Replay registers but never starts.
- **`@sentry/react` 11.11:** `export *` from `@sentry/browser`. Its `init` takes `BrowserOptions` and only adds SDK metadata. React Router tracing integrations take router functions, so they're code only, and they're named `BrowserTracing`.
- **`@sentry/nextjs` 11.11 client:** this is `index.client.js`, picked by the `browser` export condition.
  - It does `export *` from `@sentry/react`, with its own `browserTracingIntegration`.
  - `init` takes `BrowserOptions` and adds `browserTracingIntegration` and a stack-frame integration to the defaults.
  - Its types export `BrowserOptions` through the client; the server and edge entries don't define it.
- **Integration precedence:** `@sentry/core` keeps the last integration of each name, but a default instance never replaces one the user passed.
- **`transportOptions.bufferSize`:** the JavaScript counterpart of Python's `transport_queue_size`, default 64.
  - It caps the client's event processing and the transport's open requests. Anything over is dropped as `queue_overflow`, not queued.
  - Nested keys aren't converted, so it goes in `native`.
  - `Sentry.close(0)` and `flush(0)` wait forever on open requests.
- **TypeScript:** in strict mode without `allowJs`, a project fails with TS7016 when it imports a `.js` file that has no `.d.ts`.
- **PyYAML:** `safe_load` supports anchors, aliases and merge keys, and silently keeps the last of two duplicate keys.

## Not covered

- The Next.js server and edge runtimes. They'd need a Node-side bridge and option lists for `@sentry/nextjs`'s server options.
- `@sentry/node` services. Profiling there needs `@sentry/profiling-node`.
- One option list per SDK major version, picked from each project's dependencies.
