"""CI step, run on every build and release:
    python generate.py --project <name> [--release <version>] [--config <path>]
"""
import argparse, json, re, sys
from pathlib import Path

import yaml

HERE = Path(__file__).parent
SNAPSHOT_FILE = "sentry_local_config.json"
BRIDGES = {
    "python": "sentry_bridge.py",
    "django": "sentry_bridge.py",
    "javascript-browser": "sentry_bridge.js",
    "react": "sentry_bridge.js",
    "next-js": "sentry_bridge.js",  # client side only
}
# The JavaScript bridge imports @sentry/browser; each project's copy imports its own SDK package.
JS_PACKAGES = {"javascript-browser": "@sentry/browser", "react": "@sentry/react", "next-js": "@sentry/nextjs"}
# Integrations an SDK adds on its own: @sentry/nextjs adds browserTracing on the client.
DEFAULT_INTEGRATIONS = {"next-js": {"browserTracing"}}
ALLOWED = {p: set((HERE / "platforms" / f"{p}.txt").read_text().split()) for p in BRIDGES}

# Functions and objects can't live in YAML; they're passed in code.
CODE_ONLY = {
    "before_send", "before_send_transaction", "before_send_span", "before_send_log",
    "before_send_metric", "before_breadcrumb", "traces_sampler", "profiles_sampler",
    "error_sampler", "transport", "stack_parser", "event_scrubber", "custom_repr",
}
# Set by the caller (dsn, environment) or by this script (release).
CALLER_SET = {"dsn", "environment", "release"}
PROJECT_KEYS = {"root", "platform", "shared", "environments", "native"}


def camel(key):
    return re.sub(r"_([a-z])", lambda m: m.group(1).upper(), key)


def snake(key):
    return re.sub(r"(?<!^)([A-Z])", r"_\1", key).lower()


def native_name(key, platform):
    """Python SDKs use snake_case names; JavaScript SDKs use camelCase."""
    return key if BRIDGES[platform] == "sentry_bridge.py" else camel(key)


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


def check_options(where, options, platforms, native, errors):
    for key, value in (options or {}).items():
        if snake(key) in CODE_ONLY:
            errors.append(f"{where}.{key}: functions and objects must be passed in code")
        elif snake(key) in CALLER_SET:
            errors.append(f"{where}.{key}: set by the bridge call or CI, not the config")
        elif not any((key if native else native_name(key, p)) in ALLOWED[p] for p in platforms):
            errors.append(f"{where}.{key}: unknown option for {', '.join(platforms)}")
        elif key == "integrations" and not isinstance(value, (dict, type(None))):
            errors.append(f"{where}.{key}: must be a map of integration name to options")
        elif snake(key).endswith("sample_rate") and (
            isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1
        ):
            errors.append(f"{where}.{key}: must be a number from 0 to 1")


def validate(config):
    errors = []
    default = config.get("default") or {}
    envs = default.get("environments") or {}
    if not envs:
        errors.append("default: missing, or has no environments block")
    for name, block in config.items():
        block = block or {}
        if name == "default":
            platforms = list(BRIDGES)
            native = block.get("native") or {}
            for platform, options in native.items():
                if platform not in BRIDGES:
                    errors.append(f"default.native.{platform}: unknown platform")
                else:
                    check_options(f"default.native.{platform}", options, [platform], True, errors)
        else:
            if block.get("platform") not in BRIDGES:
                errors.append(f"{name}: platform must be one of {sorted(BRIDGES)}")
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
        return {k: v for k, v in converted.items() if k in ALLOWED[platform]}

    environments = {}
    for env in default.get("environments") or {}:
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
        if options.get(rate) not in (None, 0) and lifecycle in ALLOWED[platform]:
            options.setdefault(lifecycle, "trace")
        if release:
            options["release"] = release
        environments[env] = options

    return {"project": name, "platform": platform, "environments": environments}


def check_resolved(snapshot, errors):
    """Browser sample rates do nothing without their integration."""
    platform = snapshot["platform"]
    if platform not in JS_PACKAGES:
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
    parser.add_argument("--project", required=True)
    parser.add_argument("--release")
    parser.add_argument("--config", type=Path, default=HERE / "sentry_main_config.yaml",
                        help="path to the central config (default: sentry_main_config.yaml next to this script)")
    args = parser.parse_args()

    config = yaml.safe_load(args.config.read_text())
    errors = validate(config or {})
    if args.project not in (config or {}):
        errors.append(f"{args.project}: no block in {args.config.name} (needs at least root and platform)")
    if not errors:
        snapshot = resolve(config, args.project, args.release)
        check_resolved(snapshot, errors)
    if errors:
        print("Sentry config is invalid:\n  " + "\n  ".join(errors), file=sys.stderr)
        sys.exit(1)

    root = Path(config[args.project]["root"])
    root.mkdir(parents=True, exist_ok=True)
    (root / SNAPSHOT_FILE).write_text(json.dumps(snapshot, indent=2))
    platform = snapshot["platform"]
    bridge = BRIDGES[platform]
    source = (HERE / "bridges" / bridge).read_bytes()
    if platform in JS_PACKAGES:
        source = source.replace(b'from "@sentry/browser"', f'from "{JS_PACKAGES[platform]}"'.encode())
    (root / bridge).write_bytes(source)
    print(f"Wrote {root / SNAPSHOT_FILE} and {root / bridge}")


if __name__ == "__main__":
    main()
