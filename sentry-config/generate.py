"""CI step. In each project's pipeline, before every build:
    python3 generate.py --project <name> [--release <version>] [--config <path>]
In the central config's own pipeline, on every change:
    python3 generate.py --check [--config <path>]
"""
import argparse, difflib, json, re, sys
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
TYPES = "sentry_bridge.d.ts"  # copied next to the JavaScript bridge, for TypeScript projects
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
# Options with a replacement, and what to use instead.
REPLACED = {
    "disabled_integrations": "set the integration to false under integrations instead",
    "send_default_pii": "deprecated, use data_collection (dataCollection in JavaScript)",
    "enable_tracing": "deprecated, use traces_sample_rate",
}
# Options that take a list. A single value would replace the inherited list, and the
# JavaScript SDK would read a string as a list of one-character patterns.
LIST_OPTIONS = {
    "allow_urls", "deny_urls", "ignore_errors", "ignore_spans", "ignore_transactions",
    "trace_propagation_targets", "in_app_include", "in_app_exclude", "functions_to_trace",
}
# Where the Python SDK reads strings as regular expressions. Anywhere else it compares
# them as plain strings, so a {regex} entry would never match.
PYTHON_PATTERNS = {"trace_propagation_targets", "integrations"}
# What an integration's name must look like for each bridge, and how to say so.
INTEGRATION_NAMES = {
    "sentry_bridge.js": (re.compile(r"[a-z][A-Za-z0-9]*(?<!Integration)"),
                         "the factory name without Integration, like replay for replayIntegration"),
    "sentry_bridge.py": (re.compile(r"[a-z_][a-z0-9_.]*\.[A-Z]\w*"),
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


class UniqueKeyLoader(yaml.SafeLoader):
    """Fails on a duplicate key, which YAML would otherwise resolve silently to the last one."""

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    "while reading a map", node.start_mark, f"found duplicate key {key!r}", key_node.start_mark)
            seen.add(key)
        return super().construct_mapping(node, deep)


def load(path):
    """The config as a map, and any problem reading it."""
    try:
        with open(path) as stream:
            config = yaml.load(stream, Loader=UniqueKeyLoader)
    except OSError as error:
        return {}, [f"{path}: {error.strerror}"]
    except yaml.YAMLError as error:
        return {}, [str(error).replace("\n", "\n  ")]
    if not isinstance(config, (dict, type(None))):
        return {}, [f"{path}: must be a map of the default block and project blocks"]
    return config or {}, []


def as_map(where, value, errors):
    if not isinstance(value, (dict, type(None))):
        errors.append(f"{where}: must be a map")
        return {}
    return value or {}


def regexes(value):
    """The pattern of every {regex: ...} entry in a value."""
    if isinstance(value, list):
        return [p for v in value for p in regexes(v)]
    if isinstance(value, dict):
        return [value["regex"]] if value.keys() == {"regex"} else [p for v in value.values() for p in regexes(v)]
    return []


def did_you_mean(key, platforms, native):
    if native:
        names, wanted = ALLOWED[platforms[0]], native_name(snake(key), platforms[0])
    else:
        names, wanted = {snake(n) for p in platforms for n in ALLOWED[p]}, snake(key)
    match = difflib.get_close_matches(wanted, names, n=1)
    return f" (did you mean {match[0]}?)" if match else ""


def check_options(where, options, platforms, native, errors):
    for key, value in as_map(where, options, errors).items():
        name = snake(key)
        reaches = [p for p in platforms if (key if native else native_name(key, p)) in ALLOWED[p]]
        if name in CODE_ONLY:
            errors.append(f"{where}.{key}: functions and objects must be passed in code")
        elif name in CALLER_SET:
            errors.append(f"{where}.{key}: set by the bridge call or CI, not the config")
        elif name in REPLACED:
            errors.append(f"{where}.{key}: {REPLACED[name]}")
        elif not reaches:
            errors.append(f"{where}.{key}: unknown option for {', '.join(platforms)}"
                          + did_you_mean(key, platforms, native))
        elif key == "integrations" and not isinstance(value, (dict, type(None))):
            errors.append(f"{where}.{key}: must be a map of integration name to options")
        elif name.endswith("sample_rate") and (
            isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1
        ):
            errors.append(f"{where}.{key}: must be a number from 0 to 1")
        elif name in LIST_OPTIONS and not isinstance(value, list):
            errors.append(f"{where}.{key}: must be a list (put a single entry in [brackets])")
        else:
            check_values(f"{where}.{key}", key, value, reaches, errors)


def check_values(where, key, value, platforms, errors):
    """Integration names and {regex} entries, for the platforms an option reaches."""
    if key == "integrations":
        for name in value or {}:
            for bridge, (pattern, hint) in INTEGRATION_NAMES.items():
                using = [p for p in platforms if BRIDGES[p] == bridge]
                if using and not pattern.fullmatch(str(name)):
                    errors.append(f"{where}.{name}: on {', '.join(using)}, use {hint}")
    patterns = regexes(value)
    python = [p for p in platforms if BRIDGES[p] == "sentry_bridge.py"]
    if patterns and python and snake(key) not in PYTHON_PATTERNS:
        errors.append(f"{where}: the Python SDK compares these as plain strings, "
                      f"so {{regex}} entries would never match on {', '.join(python)}")
    for pattern in patterns:
        try:
            re.compile(pattern)
        except (re.error, TypeError) as error:
            errors.append(f"{where}: invalid regex {pattern!r} ({error})")


def validate(config):
    errors = []
    default = config.get("default")
    envs = default.get("environments") if isinstance(default, dict) else None
    if not envs:
        errors.append("default: missing, or has no environments block")
    for name, block in config.items():
        if not isinstance(block, (dict, type(None))):
            errors.append(f"{name}: must be a map")
            continue
        block = block or {}
        if name == "default":
            platforms = list(BRIDGES)
            for key in block.keys() - DEFAULT_KEYS:
                errors.append(f"default.{key}: unexpected key (expected {sorted(DEFAULT_KEYS)})")
            for platform, options in as_map("default.native", block.get("native"), errors).items():
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
        for env, options in as_map(f"{name}.environments", block.get("environments"), errors).items():
            if name != "default" and env not in (envs or {}):
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
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--project", help="write the files for this project")
    target.add_argument("--check", action="store_true",
                        help="validate every project and write nothing (for the central config's own CI)")
    parser.add_argument("--release")
    parser.add_argument("--config", type=Path, default=HERE / "sentry_main_config.yaml",
                        help="path to the central config (default: sentry_main_config.yaml next to this script)")
    args = parser.parse_args()

    config, errors = load(args.config)
    if not errors:
        errors = validate(config)
        if args.project and args.project not in config:
            errors.append(f"{args.project}: no block in {args.config.name} (needs at least root and platform)")
    if args.release == "":
        errors.append("--release: empty (is the version variable set?)")
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
        print("Sentry config is invalid:\n  " + "\n  ".join(errors), file=sys.stderr)
        sys.exit(1)
    if args.check:
        print(f"Sentry config is valid ({len(projects)} projects)")
        return

    snapshot = snapshots[args.project]
    platform = snapshot["platform"]
    root = Path(config[args.project]["root"])
    root.mkdir(parents=True, exist_ok=True)
    (root / SNAPSHOT_FILE).write_text(json.dumps(snapshot, indent=2))
    copies = [BRIDGES[platform]] + ([TYPES] if platform in JS_PACKAGES else [])
    for name in copies:
        source = (HERE / "bridges" / name).read_bytes()
        if platform in JS_PACKAGES:
            source = source.replace(b'from "@sentry/browser"', f'from "{JS_PACKAGES[platform]}"'.encode())
        (root / name).write_bytes(source)
    print("Wrote " + ", ".join(str(root / name) for name in [SNAPSHOT_FILE, *copies]))


if __name__ == "__main__":
    main()
