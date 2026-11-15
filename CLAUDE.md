# Central Sentry config: notes for AI coding assistants

One Sentry SDK config for every repo, distributed at build time so every team starts from the same baseline. Not an official Sentry feature. `README.md` covers setup and use. Keep user-facing writing concise.

## How it fits together

- **`sentry-config/sentry_main_config.yaml`** has a `default` block (`shared`, `environments`, `native`) and one block per project (`root` and `platform`, plus optional `shared`, `environments` and `native`).
- **`sentry-config/generate.py`**:
  - It runs as `--project X` in each app's pipeline, and as `--check` in the central repo's.
  - It validates the whole config, then resolves every project plus a new project on each platform in `default.native`. Any problem fails every build.
  - Layer order: default.shared, default.environments[env], default.native[platform], project.shared, project.environments[env], project.native. Maps merge, lists append, and single values replace.
  - `LANGUAGES` maps each platform to its SDK language, which decides its bridge files, its option list (`options/<language>.txt`) and the casing of names.
- **The bridges** (`bridges/`) are copied next to the generated `sentry_local_config.json`. For JavaScript, the copy's `from "@sentry/browser"` is rewritten to the platform's package.
  - They pick the environment at runtime, build integrations by name, turn `{regex}` into a `RegExp`, and apply code options last, with a warning on overrides.
  - When the config doesn't fit the installed SDK, they warn instead of throwing. Generate is the strict layer.

## Why it's built this way

- **Pre-merged JSON per project, not YAML read at runtime:** browsers don't download every project's config, the merge logic exists once, and CI validates exactly what ships.
- **Build time suits the goal, which is consistency:** each build ships the config it was tested with.
- **Generate copies the bridge in,** so there's no package to publish. There's one JavaScript bridge because `@sentry/react` and `@sentry/nextjs` re-export the browser integrations under the same names.

## Changing things

- **A new option:** add its SDK name to `options/<language>.txt`. If it takes a list, add it to `LIST_OPTIONS`.
- **A new platform:** add it to `LANGUAGES` (and to `JS_PACKAGES` for JavaScript), then add a project for it to `tests/fixtures/config.yaml`.
- **Generator output that changes on purpose:** run `UPDATE_GOLDEN=1 pytest` and review the diff.
- **The README's "Each app repo" step:** `test_across_repos.py` runs it as written, so keep its placeholders.

## Tests

pytest (`tests/python`) covers generate, the Python bridge, the Python option list and the README's pipeline step. Vitest (`tests/js`) covers the JavaScript bridge, the JavaScript option list and the TypeScript types. The JavaScript tests read `tests/fixtures/expected/`, so they don't need Python. `@sentry/nextjs` isn't installed, because it pulls in Next.js. Test our code, not the SDK's behavior.

Pinned versions: sentry-sdk 2.74.0, PyYAML 6.0.3, @sentry/browser and @sentry/react 11.11.0, vitest 5.1, typescript 7.0.

## SDK facts the code relies on

- **Unknown options:** Python's `sentry_sdk.init()` raises `TypeError` on them. The JavaScript SDK ignores them silently, and it reads a string `ignoreErrors` as one-character patterns.
- **sentry-sdk 2.74 deprecations:** `send_default_pii` (use `data_collection`) and `enable_tracing`.
- **Python `ignore_errors`:** it matches exact exception class names.
- **Sample rates need their integrations:**
  - `tracesSampleRate` needs `browserTracing`, the replay rates need `replay`, and profiling needs `browserProfiling` plus a `Document-Policy: js-profiling` header.
  - Continuous profiling only starts on its own with `profile_lifecycle: "trace"`.
- **Trace propagation:** setting `tracePropagationTargets` replaces the same-origin default; `{regex: "^/"}` keeps it.
- **Replay:** a second Replay instance throws in a browser, so a code-passed integration replaces the configured one rather than adding to it.
- **Integration precedence:** `@sentry/core` keeps the last integration of each name, but a default never replaces one the user passed. `@sentry/nextjs` adds its own `browserTracing`.
- **TypeScript:** strict TypeScript without `allowJs` needs `sentry_bridge.d.ts`, or the import fails with TS7016.

## Not covered

- The Next.js server and edge runtimes, and `@sentry/node` services.
- One option list per SDK major version, picked from each app's dependencies.
