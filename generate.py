"""CI step. In each app's pipeline, before every build:
    python3 generate.py --project <name> [--release <version>] [--config <path>]
In the central config repo's pipeline, on every change:
    python3 generate.py --check [--config <path>]
"""
import argparse, difflib, json, re, sys
from pathlib import Path

import yaml

HERE = Path(__file__).parent
SNAPSHOT_FILE = "sentry_local_config.json"
# Each platform's SDK language decides its bridge files, its option names and their casing.
LANGUAGES = {
    "python": "python",
    "django": "python",
    "javascript-browser": "javascript",
    "react": "javascript",
    "next-js": "javascript",  # client side only
}
BRIDGES = {"python": ["sentry_bridge.py"], "javascript": ["sentry_bridge.js", "sentry_bridge.d.ts"]}
ALLOWED = {language: set((HERE / "options" / f"{language}.txt").read_text().split()) for language in BRIDGES}
# The JavaScript bridge imports @sentry/browser; each project's copy imports its own SDK package.
JS_PACKAGES = {"javascript-browser": "@sentry/browser", "react": "@sentry/react", "next-js": "@sentry/nextjs"}
# Integrations an SDK adds on its own: @sentry/nextjs adds browserTracing on the client.
DEFAULT_INTEGRATIONS = {"next-js": {"browserTracing"}}

# Functions and objects can't live in YAML; they're passed in code.
CODE_ONLY = {
    "before_send", "before_send_transaction", "before_send_span", "before_send_log",
    "before_send_metric", "before_breadcrumb", "traces_sampler", "profiles_sampler",
    "error_sampler", "transport", "stack_parser", "event_scrubber", "custom_repr",
}
# Set by the caller (dsn, environment) or by this script (release).
CALLER_SET = {"dsn", "environment", "release"}
REPLACED = {
    "disabled_integrations": "set the integration to false under integrations instead",
    "send_default_pii": "deprecated, use data_collection (dataCollection in JavaScript)",
    "enable_tracing": "deprecated, use traces_sample_rate",
}
# A single value here would replace the inherited list, and the JavaScript SDK would
# read a string as a list of one-character patterns.
LIST_OPTIONS = {
    "allow_urls", "deny_urls", "ignore_errors", "ignore_spans", "ignore_transactions",
    "trace_propagation_targets", "in_app_include", "in_app_exclude", "functions_to_trace",
}
# A misnamed integration isn't installed, with only a warning at runtime.
INTEGRATION_NAMES = {
    "javascript": (re.compile(r"[a-z][A-Za-z0-9]*(?<!Integration)"),
                   "the factory name without Integration, like replay for replayIntegration"),
    "python": (re.compile(r"[a-z_][a-z0-9_.]*\.[A-Z]\w*"),
               "the class path under sentry_sdk.integrations, like django.DjangoIntegration"),
}
DEFAULT_KEYS = {"shared", "environments", "native"}
PROJECT_KEYS = {"root", "platform", "shared", "environments", "native"}


def camel(key):
    return re.sub(r"_([a-z])", lambda m: m.group(1).upper(), key)


def snake(key):
    return re.sub(r"(?<!^)([A-Z])", r"_\1", key).lower()


def native_name(key, platform):
    """Python SDKs use snake_case names; JavaScript SDKs use camelCase."""
    return key if LANGUAGES[platform] == "python" else camel(key)


def merge(base, override):
    """Maps merge, lists append, everything else replaces."""
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        elif isinstance(value, list) and isinstance(out.get(key), list):
            out[key] = out[key] + value
        else:
            out[key] = value
    return out


class UniqueKeyLoader(yaml.SafeLoader):
    """Fails on a duplicate key, which YAML would otherwise resolve silently to the last one."""

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":  # "<<:" keys are YAML merges, not duplicates
                continue
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    "while reading a map", node.start_mark, f"found duplicate key {key!r}", key_node.start_mark)
            seen.add(key)
        return super().construct_mapping(node, deep)


def regexes(value):
    """The pattern of every {regex: ...} entry in a value."""
    if isinstance(value, list):
        return [p for v in value for p in regexes(v)]
    if isinstance(value, dict):
        return [value["regex"]] if value.keys() == {"regex"} else [p for v in value.values() for p in regexes(v)]
    return []


def did_you_mean(key, platforms, native):
    if native:  # one platform, in its SDK's own names
        names, wanted = ALLOWED[LANGUAGES[platforms[0]]], native_name(snake(key), platforms[0])
    else:
        names, wanted = {snake(n) for p in platforms for n in ALLOWED[LANGUAGES[p]]}, snake(key)
    match = difflib.get_close_matches(wanted, names, n=1)
    return f" (did you mean {match[0]}?)" if match else ""


def check_options(where, options, platforms, native, errors):
    for key, value in (options or {}).items():
        name = snake(key)
        reaches = [p for p in platforms if (key if native else native_name(key, p)) in ALLOWED[LANGUAGES[p]]]
        if name in CODE_ONLY:
            errors.append(f"{where}.{key}: functions and objects must be passed in code")
        elif name in CALLER_SET:
            errors.append(f"{where}.{key}: set by the bridge call or CI, not the config")
        elif name in REPLACED:
            errors.append(f"{where}.{key}: {REPLACED[name]}")
        elif not reaches:
            errors.append(f"{where}.{key}: unknown option for {', '.join(platforms)}"
                          + did_you_mean(key, platforms, native))
        elif name.endswith("sample_rate") and (
            isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1
        ):
            errors.append(f"{where}.{key}: must be a number from 0 to 1")
        elif name in LIST_OPTIONS and not isinstance(value, list):
            errors.append(f"{where}.{key}: must be a list (put a single entry in [brackets])")
        elif key == "integrations":
            check_integrations(f"{where}.{key}", value, reaches, errors)
        for pattern in regexes(value):
            try:
                re.compile(pattern)
            except (re.error, TypeError) as error:
                errors.append(f"{where}.{key}: invalid regex {pattern!r} ({error})")


def check_integrations(where, integrations, platforms, errors):
    if not isinstance(integrations, (dict, type(None))):
        errors.append(f"{where}: must be a map of integration name to options")
        return
    for name in integrations or {}:
        for language, (pattern, hint) in INTEGRATION_NAMES.items():
            using = [p for p in platforms if LANGUAGES[p] == language]
            if using and not pattern.fullmatch(str(name)):
                errors.append(f"{where}.{name}: on {', '.join(using)}, use {hint}")


def validate(config):
    errors = []
    default = config.get("default") or {}
    envs = default.get("environments") or {}
    if not envs:
        errors.append("default: missing, or has no environments block")
    for name, block in config.items():
        block = block or {}
        if name == "default":
            platforms = list(LANGUAGES)
            for key in block.keys() - DEFAULT_KEYS:
                errors.append(f"default.{key}: unexpected key (expected {sorted(DEFAULT_KEYS)})")
            for platform, options in (block.get("native") or {}).items():
                if platform not in LANGUAGES:
                    errors.append(f"default.native.{platform}: unknown platform")
                else:
                    check_options(f"default.native.{platform}", options, [platform], True, errors)
        else:
            if block.get("platform") not in LANGUAGES:
                errors.append(f"{name}: platform must be one of {sorted(LANGUAGES)}")
                continue
            if not block.get("root"):
                errors.append(f"{name}: missing root")
            for key in block.keys() - PROJECT_KEYS:
                errors.append(f"{name}.{key}: unexpected key (expected {sorted(PROJECT_KEYS)})")
            platforms = [block["platform"]]
            check_options(f"{name}.native", block.get("native"), platforms, True, errors)
        check_options(f"{name}.shared", block.get("shared"), platforms, False, errors)
        for env, options in (block.get("environments") or {}).items():
            if name != "default" and env not in envs:
                errors.append(f"{name}.environments.{env}: not defined in default")
            check_options(f"{name}.environments.{env}", options, platforms, False, errors)
    return errors


def resolve(config, name, release):
    default, project = config["default"], config[name]
    platform = project["platform"]

    def shared(options):
        converted = {native_name(k, platform): v for k, v in (options or {}).items()}
        return {k: v for k, v in converted.items() if k in ALLOWED[LANGUAGES[platform]]}

    environments = {}
    for env in default["environments"]:
        options = {}
        for layer in (
            shared(default.get("shared")),
            shared(default["environments"][env]),
            (default.get("native") or {}).get(platform),
            shared(project.get("shared")),
            shared((project.get("environments") or {}).get(env)),
            project.get("native"),
        ):
            options = merge(options, layer)

        # Continuous profiling only starts on its own with the "trace" lifecycle.
        rate, lifecycle = native_name("profile_session_sample_rate", platform), native_name("profile_lifecycle", platform)
        if options.get(rate) not in (None, 0):
            options.setdefault(lifecycle, "trace")
        if release:
            options["release"] = release
        environments[env] = options

    return {"project": name, "platform": platform, "environments": environments}


def check_resolved(snapshot, errors):
    """Browser sample rates do nothing without their integration."""
    platform = snapshot["platform"]
    if LANGUAGES[platform] != "javascript":
        return
    needs = {"tracesSampleRate": "browserTracing", "replaysSessionSampleRate": "replay",
             "replaysOnErrorSampleRate": "replay", "profileSessionSampleRate": "browserProfiling"}
    defaults = DEFAULT_INTEGRATIONS.get(platform, set())
    for env, options in snapshot["environments"].items():
        integrations = options.get("integrations") or {}
        for rate, integration in needs.items():
            enabled = integrations.get(integration, integration in defaults) is not False
            if options.get(rate) not in (None, 0) and not enabled:
                errors.append(f"{snapshot['project']} ({env}): {rate} is set but the {integration} integration isn't")


def main():
    parser = argparse.ArgumentParser()
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--project", help="write the files for this project")
    target.add_argument("--check", action="store_true", help="validate every project and write nothing")
    parser.add_argument("--release")
    parser.add_argument("--config", type=Path, default=HERE / "sentry_main_config.yaml")
    args = parser.parse_args()

    try:
        with open(args.config) as stream:
            config = yaml.load(stream, Loader=UniqueKeyLoader) or {}
    except yaml.YAMLError as error:
        sys.exit(f"Sentry config is invalid:\n  {error}")
    errors = validate(config)
    if args.project and args.project not in config:
        errors.append(f"{args.project}: no block in {args.config.name} (needs at least root and platform)")
    if not errors:
        # Check every project, and a new project on each platform in default.native,
        # so a problem anywhere, even in the defaults, fails every build.
        projects = {name: block for name, block in config.items() if name != "default"}
        new = {f"a new {p} project": {"root": ".", "platform": p} for p in config["default"].get("native") or {}}
        snapshots = {name: resolve({**config, name: block}, name, args.release)
                     for name, block in {**projects, **new}.items()}
        for snapshot in snapshots.values():
            check_resolved(snapshot, errors)
    if errors:
        sys.exit("Sentry config is invalid:\n  " + "\n  ".join(errors))
    if args.check:
        print(f"Sentry config is valid ({len(projects)} projects)")
        return

    platform = config[args.project]["platform"]
    root = Path(config[args.project]["root"])
    root.mkdir(parents=True, exist_ok=True)
    (root / SNAPSHOT_FILE).write_text(json.dumps(snapshots[args.project], indent=2))
    files = BRIDGES[LANGUAGES[platform]]
    for name in files:
        source = (HERE / "bridges" / name).read_text()
        if platform in JS_PACKAGES:
            source = source.replace('from "@sentry/browser"', f'from "{JS_PACKAGES[platform]}"')
        (root / name).write_text(source)
    print(f"Wrote {', '.join([SNAPSHOT_FILE, *files])} to {root}")


if __name__ == "__main__":
    main()
