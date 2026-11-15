# Central Sentry config

A reference implementation of one Sentry SDK config shared by every repo, so every team starts from the same baseline and changes reach each app with its next build. This is not an official Sentry feature.

## How it works

```mermaid
flowchart TD
    config["1. Fill out the config<br/>sentry_main_config.yaml"]
    lists["platforms/*.txt<br/>allowed option names"]
    source["bridges/<br/>bridge source files"]
    generate["2. CI runs generate.py<br/>before each build"]
    fail["Build fails and<br/>lists every problem"]
    subgraph out["3. Written to the project's root"]
        json["sentry_local_config.json<br/>options for each<br/>environment"]
        bridge["sentry_bridge.js and .d.ts,<br/>or sentry_bridge.py"]
    end
    app["4. The app imports<br/>getSentryOptions and<br/>passes the project,<br/>environment and DSN"]
    sdk["Sentry.init(options)"]

    config --> generate
    lists -.-> generate
    source -.-> generate
    generate -->|invalid| fail
    generate -->|valid| out
    json -->|read at runtime| bridge
    bridge --> app
    app --> sdk
```

1. **Fill out the config.** `sentry-config/sentry_main_config.yaml` holds the defaults plus one block per project. Each project block sets its `platform` and its `root`, the folder that gets the generated files.
2. **CI runs `generate.py`** for the project being built. It checks the whole config against the option names in `sentry-config/platforms/` and fails the build if anything is wrong. The lists match `@sentry/browser`, `@sentry/react` and `@sentry/nextjs` 11.11 and `sentry-sdk` 2.74.
3. **It writes the project's files into its `root`.** `sentry_local_config.json` holds the resolved options for every environment. Next to it goes a copy of the bridge: `sentry_bridge.js` (with `sentry_bridge.d.ts` for TypeScript) for `javascript-browser`, `react` and `next-js`, or `sentry_bridge.py` for `python` and `django`.
4. **The app imports `getSentryOptions`** (`get_sentry_options` in Python) and passes its project name, environment and DSN. The bridge picks that environment's options at runtime.

Platforms: `python`, `django`, `javascript-browser`, `react` and `next-js` (client side).

## Try it

From this folder:

```
python3 -m pip install -r sentry-config/requirements.txt
python3 sentry-config/generate.py --check
python3 sentry-config/generate.py --project web-app --release 1.0.0
python3 sentry-config/generate.py --project api --release 1.0.0
```

The last two write into `examples/`, whose init files show the app side. Pass `--config <path>` to use a config file somewhere else.

## Setting it up

### The central repo

1. Copy `sentry-config/`. To keep the tests, also copy `tests/`, `package.json`, `package-lock.json`, `requirements-dev.txt`, `pytest.ini` and `vitest.config.js`.
2. Replace the example defaults and projects in `sentry_main_config.yaml` with your own (see [Writing the config](#writing-the-config)).
3. On every change, run `python3 sentry-config/generate.py --check` and the tests. `.github/workflows/check.yml` does this on GitHub Actions. Make it a required check: a broken config fails every app's build.

### Each app repo

1. **Add a block for the app** to the central config, with its `root` and `platform`.
2. **Add the Sentry step to its pipeline**, before the build:

   ```sh
   config=$(mktemp -d)
   git clone --depth 1 --branch main <central config repo> "$config"
   python3 -m pip install -r "$config/sentry-config/requirements.txt"
   python3 "$config/sentry-config/generate.py" --project <name> --release <version>
   ```

   - Run it from the repo's root: `root` is relative to the folder generate runs from.
   - The pipeline needs read access to the central repo, plus Python 3 and pip. Where pip won't install outside a virtual environment, create one first.
   - Cloning `main` means every build gets the current baseline.

3. **Gitignore the generated files:** `sentry_local_config.json`, `sentry_bridge.js`, `sentry_bridge.d.ts` and `sentry_bridge.py`. Developers run the same step before running the app locally.
4. **Initialize Sentry through the bridge** (see [In the app](#in-the-app)).

## Writing the config

The config has a `default` block and one block per project. Its comments explain each part.

- **Layers.** For each environment, a project's options are built from `default.shared`, `default.environments.<env>`, `default.native.<platform>`, then the project's own `shared`, `environments.<env>` and `native`. Later layers win.
- **Merging.** Maps merge and single values replace. Lists append, so a project can't remove an entry it inherits. Keep default lists to entries every project wants.
- **Shared and native options.** Shared options use snake_case and are converted to each SDK's names. A platform without the option doesn't get it. Native options use one SDK's own names and value formats, and apply after environments. So put per-environment values, like sample rates, in `environments`.
- **Environments.** `default.environments` lists every environment the apps report. Projects can override values only for those.
- **Patterns.** In JavaScript, a plain string is a substring match and `{regex: "..."}` becomes a `RegExp`. In Python, only `trace_propagation_targets` and integration options read regular expressions, so generate rejects `{regex}` anywhere else in Python.
- **Integrations.** A map of name to options.
  - JavaScript names drop the `Integration` suffix: `replay` builds `replayIntegration()`.
  - Python names are the class path under `sentry_sdk.integrations`, like `django.DjangoIntegration`.
  - `{}`, `true` or an empty value means default options. `false` turns off an integration the SDK adds by default.
- **Not in the config.** Callbacks, samplers and the transport are passed in code. `dsn` and `environment` come from the bridge call, and `release` comes from `--release`.
- **Profiling.** When a profile sample rate is above 0, generate sets the profile lifecycle to `"trace"`, so profiling runs with each sampled trace.
- **Checks.** generate fails on any of these, and every build checks every project, plus a new project on each platform in `default.native`:
  - unknown options (it suggests the closest name) and deprecated ones
  - values of the wrong type, and invalid regexes
  - malformed integration names
  - browser sample rates whose integration is missing

## In the app

```js
import * as Sentry from "@sentry/browser"; // the package the bridge copy imports
import { getSentryOptions } from "./sentry_bridge.js";

Sentry.init(getSentryOptions("web-app", environment, dsn, { beforeSend }));
```

```python
from sentry_bridge import get_sentry_options

sentry_sdk.init(**get_sentry_options("api", environment, dsn, before_send=before_send))
```

- **The last argument is optional.** Use it for callbacks and anything else that can't be written as data.
  - It's applied last, and the bridge warns when it overrides a configured value.
  - Integrations passed there are added, and replace a configured integration with the same name.
- **Pick the environment at runtime,** as `examples/web-app/src/instrument.js` does, so a build promoted from staging to production reports production values.
- **Import `init` from the package the bridge copy imports:** `@sentry/browser`, `@sentry/react` or `@sentry/nextjs`.
- **When the config doesn't fit the installed SDK, the bridges warn instead of failing.** That covers unknown integrations, an option the SDK lacks, or an invalid regex.
- **React:** React Router tracing integrations take router functions, so pass them in code. They replace the configured `browserTracing` because they share its name.
- **Next.js:** client side only. Import the bridge from `instrumentation-client.js`.

## Tests

```
python3 -m pip install -r requirements-dev.txt
npm install
pytest
npm test
```

- **Generator** (`tests/python/test_generate.py`): merge rules, per-platform resolution, every validation error, and the command line.
  - The full output for each project in `tests/fixtures/config.yaml` is compared with the snapshots in `tests/fixtures/expected/`.
  - It loads the bridge from the folder `generate.py` wrote, so a filename mismatch between the two fails the suite.
  - It also runs `--check` on the shipped config.
- **Bridges** (`tests/python/test_bridge.py`, `tests/js/*.test.js`): each bridge is loaded the way a project gets it, next to an expected snapshot, and run against the real SDK (`sentry-sdk`, `@sentry/browser`, `@sentry/react`).
  - The tests check what the bridge returns, what the SDK ends up configured with, and what it sends. A test transport records what would be sent, so nothing leaves the machine.
  - A TypeScript check compiles the types file in strict mode.
  - `@sentry/nextjs` isn't installed, because it pulls in Next.js.
- **Option lists** (`tests/python/test_option_lists.py`, `tests/js/option-list.test.js`): check every name in `sentry-config/platforms/` against the installed SDK. The JavaScript check uses the SDK's `BrowserOptions` type, because the JavaScript SDK ignores unknown options without a warning.
- **Across repos** (`tests/python/test_across_repos.py`): runs the pipeline step from [Each app repo](#each-app-repo) exactly as written, in an app repo separate from the central repo. It checks that a central change reaches the app on its next build.

The JavaScript tests read the snapshots in `tests/fixtures/expected/`, which the Python tests prove match the generator's output, so they don't need Python.

### When the generator's output changes on purpose

Regenerate the expected snapshots, then review the diff before committing:

```
UPDATE_GOLDEN=1 pytest
```

### Other SDK versions

The pinned versions in `requirements-dev.txt` and `package.json` match the option lists. To check a version your apps use, install it and rerun the suite:

```
python3 -m pip install sentry-sdk==<version> && pytest
npm install -D @sentry/browser@<version> @sentry/react@<version> && npm test
```

A failing option-list test means an option was renamed or removed in that version. Update the lists when your apps move to a new SDK major version.
