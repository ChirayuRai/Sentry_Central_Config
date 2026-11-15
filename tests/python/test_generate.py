"""Tests for the CI step (sentry-config/generate.py)."""
import json
import os
import re
import subprocess
import sys

import pytest
import yaml

import generate
from conftest import (CONFIG_DIR, DSN, EXPECTED, FIXTURE_CONFIG, PLATFORMS, PROJECTS, RELEASE, golden,
                      import_file)


def run_generate(cwd, *args, config=FIXTURE_CONFIG):
    return subprocess.run(
        [sys.executable, str(CONFIG_DIR / "generate.py"), "--config", str(config), *args],
        cwd=cwd, capture_output=True, text=True,
    )


def generated(tmp_path, project):
    """Run generate.py for a fixture project and return the folder it wrote."""
    result = run_generate(tmp_path, "--project", project, "--release", RELEASE)
    assert result.returncode == 0, result.stderr
    return tmp_path / "out" / project


def write_config(tmp_path, config):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


def resolved(config, project, env, release=None):
    return generate.resolve(config, project, release)["environments"][env]


# --- Full output -------------------------------------------------------------

@pytest.mark.parametrize("project", PROJECTS)
def test_output_matches_golden_file(project, tmp_path):
    snapshot = json.loads((generated(tmp_path, project) / generate.SNAPSHOT_FILE).read_text())
    expected = EXPECTED / f"{project}.json"
    if os.environ.get("UPDATE_GOLDEN"):
        expected.parent.mkdir(exist_ok=True)
        expected.write_text(json.dumps(snapshot, indent=2) + "\n")
    assert snapshot == json.loads(expected.read_text())


# --- What a project gets -----------------------------------------------------
# These run the bridge copy generate.py wrote, next to the snapshot it wrote,
# so a filename mismatch between the two fails here.

@pytest.mark.parametrize("project, files", [
    ("web-app", ["sentry_bridge.js", "sentry_bridge.d.ts"]),
    ("api", ["sentry_bridge.py"]),
])
def test_writes_the_snapshot_and_the_matching_bridge_only(project, files, tmp_path):
    out = generated(tmp_path, project)
    assert sorted(p.name for p in out.iterdir()) == sorted([generate.SNAPSHOT_FILE, *files])


@pytest.mark.parametrize("project, sdk", [
    ("web-app", "@sentry/browser"),
    ("react-app", "@sentry/react"),
    ("next-app", "@sentry/nextjs"),
])
def test_javascript_bridge_imports_the_platform_sdk_and_the_snapshot(project, sdk, tmp_path):
    out = generated(tmp_path, project)
    source = (out / "sentry_bridge.js").read_text()
    imports = re.findall(r'^import .+ from "([^"]+)";$', source, re.MULTILINE)
    local = [path for path in imports if path.startswith(".")]
    assert [path for path in imports if path not in local] == [sdk]
    assert local and all((out / path).is_file() for path in local)
    # Apart from that import, the copy is the bridge unchanged.
    original = (CONFIG_DIR / "bridges" / "sentry_bridge.js").read_text()
    assert source.replace(f'from "{sdk}"', 'from "@sentry/browser"') == original
    # The types file imports the same SDK.
    assert re.findall(r'from "([^"]+)"', (out / "sentry_bridge.d.ts").read_text()) == [sdk]


@pytest.mark.parametrize("project", ["api", "django-app"])
def test_python_bridge_loads_the_snapshot(project, tmp_path):
    out = generated(tmp_path, project)
    assert (out / "sentry_bridge.py").read_bytes() == (CONFIG_DIR / "bridges" / "sentry_bridge.py").read_bytes()
    bridge = import_file(out / "sentry_bridge.py")
    assert bridge.SNAPSHOT == golden(project)
    options = bridge.get_sentry_options(project, "staging", DSN)
    assert options["traces_sample_rate"] == 0.5
    assert options["release"] == RELEASE


def test_release_is_set_in_every_environment_when_passed(config):
    for env in ("production", "staging"):
        assert "release" not in resolved(config, "api", env)
        assert resolved(config, "api", env, release="2.0.0")["release"] == "2.0.0"


# --- Merge rules -------------------------------------------------------------

def test_merge_combines_maps_recursively():
    base = {"integrations": {"replay": {"maskAllText": True, "blockAllMedia": True}}}
    override = {"integrations": {"replay": {"maskAllText": False}, "dedupe": False}}
    assert generate.merge(base, override) == {
        "integrations": {"replay": {"maskAllText": False, "blockAllMedia": True}, "dedupe": False},
    }


def test_merge_appends_lists():
    assert generate.merge({"ignoreErrors": ["a"]}, {"ignoreErrors": ["b"]}) == {"ignoreErrors": ["a", "b"]}


def test_merge_replaces_single_values():
    assert generate.merge({"sampleRate": 1.0}, {"sampleRate": 0.5}) == {"sampleRate": 0.5}


def test_merge_lets_false_replace_a_map():
    assert generate.merge({"replay": {"maskAllText": True}}, {"replay": False}) == {"replay": False}


def test_merge_does_not_modify_its_inputs():
    base, override = {"a": {"b": [1]}}, {"a": {"b": [2]}}
    generate.merge(base, override)
    assert base == {"a": {"b": [1]}} and override == {"a": {"b": [2]}}


# --- Resolving a project -----------------------------------------------------

def test_new_project_gets_the_defaults_for_every_environment(config):
    snapshot = generate.resolve(config, "worker", None)
    assert set(snapshot["environments"]) == {"production", "staging"}
    staging = snapshot["environments"]["staging"]
    assert staging["traces_sample_rate"] == 1.0
    assert staging["debug"] is True
    assert staging["max_breadcrumbs"] == 50


def test_environment_override_only_changes_that_environment(config):
    assert resolved(config, "web-app", "production")["tracesSampleRate"] == 0.2
    assert resolved(config, "web-app", "staging")["tracesSampleRate"] == 1.0


def test_project_shared_overrides_default_shared_in_every_environment(config):
    for env in ("production", "staging"):
        assert resolved(config, "api", env)["max_breadcrumbs"] == 20


@pytest.mark.parametrize("project", PROJECTS)
def test_names_are_converted_for_javascript_and_kept_for_python(config, project):
    options = resolved(config, project, "production")
    if generate.LANGUAGES[PLATFORMS[project]] == "python":
        assert {"traces_sample_rate", "max_breadcrumbs", "attach_stacktrace"} <= options.keys()
        assert "tracesSampleRate" not in options
    else:
        assert {"tracesSampleRate", "maxBreadcrumbs", "attachStacktrace"} <= options.keys()
        assert "traces_sample_rate" not in options


def test_django_gets_shared_options_and_its_own_native_ones(config):
    production = resolved(config, "django-app", "production")
    assert production["max_breadcrumbs"] == 30
    assert production["profile_lifecycle"] == "trace"
    assert production["enable_logs"] is True
    assert production["integrations"] == {"django.DjangoIntegration": {"transaction_style": "url"}}
    assert "ignore_errors" not in production  # default.native.python doesn't reach django
    assert resolved(config, "django-app", "staging")["traces_sample_rate"] == 0.5


def test_options_a_platform_lacks_are_dropped(config):
    py = resolved(config, "api", "production")
    assert not any(key.startswith("replays") for key in py)


def test_default_native_options_only_reach_their_platform(config):
    js = resolved(config, "web-app", "production")
    py = resolved(config, "api", "production")
    assert "include_local_variables" not in js and "ignore_errors" not in js
    assert "ignoreErrors" not in py and "denyUrls" not in py


def test_project_lists_are_appended_to_the_defaults(config):
    assert resolved(config, "web-app", "production")["ignoreErrors"] == [
        "ResizeObserver loop limit exceeded",
        {"regex": "^AbortError"},
        "Non-Error promise rejection captured",
    ]
    assert resolved(config, "api", "production")["ignore_errors"] == ["KeyboardInterrupt", "ConnectionResetError"]


def test_project_integration_options_merge_with_the_defaults(config):
    integrations = resolved(config, "web-app", "production")["integrations"]
    assert integrations["replay"] == {"maskAllText": False, "blockAllMedia": True}
    assert integrations["browserTracing"] == {}
    assert integrations["dedupe"] is False


def test_native_options_win_over_shared_ones_at_the_same_level(config):
    # default.environments sets a 0.1 profiling rate; default.native sets 0 for the browser.
    assert resolved(config, "web-app", "production")["profileSessionSampleRate"] == 0


def test_profile_lifecycle_is_set_only_when_profiling_is_on(config):
    assert resolved(config, "api", "production")["profile_lifecycle"] == "trace"
    assert "profile_lifecycle" not in resolved(config, "api", "staging")
    assert "profileLifecycle" not in resolved(config, "web-app", "production")


def test_an_explicit_profile_lifecycle_is_kept(config):
    config["api"]["native"]["profile_lifecycle"] = "manual"
    assert resolved(config, "api", "production")["profile_lifecycle"] == "manual"


def test_a_zero_override_is_kept(config):
    config["web-app"]["environments"]["staging"] = {"traces_sample_rate": 0}
    assert resolved(config, "web-app", "staging")["tracesSampleRate"] == 0


# --- Validation --------------------------------------------------------------

def test_fixture_config_is_valid(config):
    assert generate.validate(config) == []
    for project in PROJECTS:
        errors = []
        generate.check_resolved(generate.resolve(config, project, None), errors)
        assert errors == []


def _set(path, value):
    def mutate(config):
        target = config
        for key in path[:-1]:
            target = target.setdefault(key, {})
        target[path[-1]] = value
    return mutate


def _delete(*path):
    def mutate(config):
        target = config
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]
    return mutate


INVALID = {
    "callback": (
        _set(["web-app", "native", "beforeSend"], "dropHealthChecks"),
        "web-app.native.beforeSend: functions and objects must be passed in code"),
    "dsn": (
        _set(["api", "native", "dsn"], "https://key@o0.ingest.sentry.io/0"),
        "api.native.dsn: set by the bridge call or CI"),
    "deprecated option": (
        _set(["django-app", "native", "send_default_pii"], True),
        "django-app.native.send_default_pii: deprecated, use data_collection"),
    "typo": (
        _set(["web-app", "native", "tracesSampleRte"], 0.1),
        "web-app.native.tracesSampleRte: unknown option for javascript-browser (did you mean tracesSampleRate?)"),
    "JavaScript name on a Python platform": (
        _set(["django-app", "native", "maxBreadcrumbs"], 10),
        "django-app.native.maxBreadcrumbs: unknown option for django (did you mean max_breadcrumbs?)"),
    "option the platform lacks": (
        _set(["api", "environments", "production", "replays_session_sample_rate"], 0.1),
        "api.environments.production.replays_session_sample_rate: unknown option for python"),
    "rate above 1": (
        _set(["default", "environments", "production", "traces_sample_rate"], 1.5),
        "default.environments.production.traces_sample_rate: must be a number from 0 to 1"),
    "rate as a boolean": (
        _set(["default", "environments", "staging", "traces_sample_rate"], True),
        "default.environments.staging.traces_sample_rate: must be a number from 0 to 1"),
    "list as a single value": (
        _set(["web-app", "native", "ignoreErrors"], "ResizeObserver"),
        "web-app.native.ignoreErrors: must be a list"),
    "invalid regex": (
        _set(["web-app", "native", "denyUrls"], [{"regex": "("}]),
        "web-app.native.denyUrls: invalid regex '('"),
    "integrations as a list": (
        _set(["react-app", "native", "integrations"], ["replay"]),
        "react-app.native.integrations: must be a map of integration name to options"),
    "JavaScript integration name": (
        _set(["web-app", "native", "integrations", "Replay"], {}),
        "web-app.native.integrations.Replay: on javascript-browser, use the factory name without Integration"),
    "Python integration name": (
        _set(["api", "native", "integrations", "DedupeIntegration"], False),
        "api.native.integrations.DedupeIntegration: on python, use the class path under sentry_sdk.integrations"),
    "environment typo": (
        _set(["web-app", "environments", "prodution"], {"traces_sample_rate": 0.2}),
        "web-app.environments.prodution: not defined in default"),
    "missing root": (
        _delete("web-app", "root"),
        "web-app: missing root"),
    "unknown platform": (
        _set(["web-app", "platform"], "ruby"),
        "web-app: platform must be one of"),
    "unexpected project key": (
        _set(["api", "production"], {"traces_sample_rate": 0.2}),
        "api.production: unexpected key"),
    "unexpected default key": (
        _set(["default", "sharred"], {"attach_stacktrace": True}),
        "default.sharred: unexpected key"),
    "unknown platform in default.native": (
        _set(["default", "native", "ruby"], {}),
        "default.native.ruby: unknown platform"),
    "missing default": (
        _delete("default"),
        "default: missing, or has no environments block"),
}


@pytest.mark.parametrize("mutate, message", INVALID.values(), ids=INVALID.keys())
def test_invalid_config_is_reported(config, mutate, message):
    mutate(config)
    errors = generate.validate(config)
    assert any(message in error for error in errors), errors


def test_every_problem_is_reported_at_once(config):
    INVALID["missing root"][0](config)
    INVALID["rate above 1"][0](config)
    assert len(generate.validate(config)) == 2


def _integration_errors(config, project):
    errors = []
    generate.check_resolved(generate.resolve(config, project, None), errors)
    return errors


def test_traces_rate_without_browser_tracing_fails(config):
    config["web-app"]["native"]["integrations"]["browserTracing"] = False
    assert "web-app (production): tracesSampleRate is set but the browserTracing integration isn't" \
        in _integration_errors(config, "web-app")


def test_next_js_has_browser_tracing_unless_turned_off(config):
    assert _integration_errors(config, "next-app") == []
    config["next-app"]["native"] = {"integrations": {"browserTracing": False}}
    assert "next-app (production): tracesSampleRate is set but the browserTracing integration isn't" \
        in _integration_errors(config, "next-app")


def test_zero_rate_needs_no_integration(config):
    config["web-app"]["environments"] = {
        "production": {"traces_sample_rate": 0},
        "staging": {"traces_sample_rate": 0},
    }
    config["web-app"]["native"]["integrations"]["browserTracing"] = False
    assert _integration_errors(config, "web-app") == []


def test_integration_with_no_options_counts_as_enabled(config):
    # "browserTracing:" with nothing after it loads as None.
    config["default"]["native"]["javascript-browser"]["integrations"]["browserTracing"] = None
    assert _integration_errors(config, "web-app") == []


# --- Command line ------------------------------------------------------------

def test_invalid_config_fails_the_build_and_writes_nothing(config, tmp_path):
    INVALID["rate above 1"][0](config)
    result = run_generate(tmp_path, "--project", "web-app", config=write_config(tmp_path, config))
    assert result.returncode == 1
    assert "traces_sample_rate: must be a number from 0 to 1" in result.stderr
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("break_it, message", [
    (lambda c: c["worker"].update(platform="ruby"), "worker: platform must be one of"),
    (lambda c: c["web-app"]["native"]["integrations"].update(browserTracing=False),
     "web-app (production): tracesSampleRate is set but the browserTracing integration isn't"),
], ids=["invalid value", "missing integration"])
def test_a_problem_in_another_project_fails_every_build(config, tmp_path, break_it, message):
    break_it(config)
    result = run_generate(tmp_path, "--project", "api", config=write_config(tmp_path, config))
    assert result.returncode == 1
    assert message in result.stderr


def test_check_validates_every_project_and_writes_nothing(tmp_path):
    result = run_generate(tmp_path, "--check")
    assert result.returncode == 0, result.stderr
    assert f"valid ({len(PROJECTS)} projects)" in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_check_covers_a_new_project_on_each_default_platform(config, tmp_path):
    # With no react project in the config, only a new one would hit this.
    del config["react-app"]
    del config["default"]["native"]["react"]["integrations"]["browserTracing"]
    result = run_generate(tmp_path, "--check", config=write_config(tmp_path, config))
    assert result.returncode == 1
    assert "a new react project (production): tracesSampleRate is set but the browserTracing integration isn't" \
        in result.stderr


def test_the_shipped_config_is_valid(tmp_path):
    result = run_generate(tmp_path, "--check", config=CONFIG_DIR / "sentry_main_config.yaml")
    assert result.returncode == 0, result.stderr


def test_a_duplicate_key_fails(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("default:\n  environments: {production: {}}\n"
                    "api: {root: a, platform: python}\napi: {root: b, platform: python}\n")
    result = run_generate(tmp_path, "--check", config=path)
    assert result.returncode == 1
    assert "found duplicate key 'api'" in result.stderr
    assert "Traceback" not in result.stderr


def test_unknown_project_fails(tmp_path):
    result = run_generate(tmp_path, "--project", "billing")
    assert result.returncode == 1
    assert "billing: no block in config.yaml" in result.stderr


def test_empty_config_file_fails_cleanly(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("")
    result = run_generate(tmp_path, "--project", "web-app", config=path)
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
