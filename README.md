# Central Sentry config

One Sentry SDK config for all your apps. Each app's build reads its settings from here, so every team starts from the same baseline and a change reaches each app with its next build. This is a reference implementation, not an official Sentry feature.

## What's here

| File | What it is |
|---|---|
| `sentry_main_config.yaml` | The config: defaults, plus one block per app. |
| `generate.py` | Runs in each app's pipeline. Checks the config and writes that app's settings into it. |
| `bridges/` | Copied into each app by `generate.py`. The app calls the bridge to get its options. |
| `options/` | The option names `generate.py` accepts, per SDK language. |
| `examples/` | Sentry init code for a browser app and a Python service. |

Supported platforms: `python`, `django`, `javascript-browser`, `react` and `next-js` (client side). The option lists match `sentry-sdk` 2.74 and the JavaScript SDKs 11.11.

## Try it

```
python3 -m pip install -r requirements.txt
python3 generate.py --check
python3 generate.py --project web-app --release 1.0.0
```

The last command writes the web app's settings (`sentry_local_config.json`) and the bridge into `examples/web-app/`, next to its init code.

## Setting it up

### The central repo

Put these files in a repo of their own, and replace the example defaults and apps in `sentry_main_config.yaml` with yours. Run `python3 generate.py --check` on every change, and make it a required check: a broken config fails every app's build.

### Each app

1. Add the app's block to the central config. It needs a `platform` and a `root`, the folder that gets the generated files, relative to the app repo's root.
2. Add this step to the app's pipeline, before the build. Run it from the app repo's root. It needs Python 3 and read access to the central repo. Developers run the same step before running the app locally.

   ```sh
   config=$(mktemp -d)
   git clone --depth 1 --branch main <central config repo> "$config"
   python3 -m pip install -r "$config/requirements.txt"
   python3 "$config/generate.py" --project <name> --release <version>
   ```

3. Gitignore the generated files: `sentry_local_config.json`, `sentry_bridge.js`, `sentry_bridge.d.ts` and `sentry_bridge.py`.
4. Initialize Sentry through the bridge, as the files in `examples/` do:

   ```js
   Sentry.init(getSentryOptions("web-app", environment, dsn, { beforeSend }));
   ```

   ```python
   sentry_sdk.init(**get_sentry_options("api", environment, dsn, before_send=before_send))
   ```

   - Import `Sentry` from the package the app uses: `@sentry/browser`, `@sentry/react` or `@sentry/nextjs`. Next.js apps do this in `instrumentation-client.ts`.
   - Pick the environment at runtime, so a build promoted from staging to production reports as production.

## Moving an existing app over

1. Move each option the app passes to `Sentry.init` into its block in the config, or drop it where the defaults already cover it.
2. Keep callbacks, samplers and integrations that take functions, such as React Router's, in code. They go in the last argument.
3. The app still passes `dsn` and `environment`. The `release` comes from `--release`.

If code still sets a value the config also sets, the bridge logs a warning, so leftovers show up in the console.

## Writing the config

- **Layers.** Each environment's options are built from `default.shared`, `default.environments.<env>` and `default.native.<platform>`, then from the app's own `shared`, `environments.<env>` and `native`. Later layers win.
- **Merging.** Maps merge and single values replace. Lists append, so an app can't remove an entry it inherits.
- **Shared and native options.** Shared options use snake_case and are converted to each SDK's names. A platform without the option doesn't get it. Native options use the SDK's own names and apply after environments, so keep per-environment values, like sample rates, in `environments`.
- **Patterns.** In JavaScript, a string matches anywhere in the message, and `{regex: "..."}` becomes a `RegExp`. In Python, `ignore_errors` matches exception class names, not patterns.
- **Integrations.** A map of names to options. In JavaScript, `replay` builds `replayIntegration()`; in Python, `django.DjangoIntegration` is the class under `sentry_sdk.integrations`. Use `{}` for default options, and `false` to turn off an integration the SDK adds by default.
- **Callbacks** go in code, not the config.
- **Checks.** `generate.py` fails on unknown or deprecated options, lists written as a single value, invalid regexes, misnamed integrations, and browser sample rates whose integration is missing. It checks every app, plus a new app on each platform in `default.native`.
- **SDK upgrades.** When your apps move to a new SDK major version, check its changelog for renamed or removed options and update `options/`.
